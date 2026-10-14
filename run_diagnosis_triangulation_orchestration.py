#!/usr/bin/env python3
"""Diagnosis-mechanism triangulation and staged orchestration (2026-09-22).

Brief: TASK_BRIEF_DIAGNOSIS_TRIANGULATION_ORCHESTRATION_2026-09-22.md

Structural model comparison for a preliminary result: not production adoption and
not an HTA baseline. M0 is the legacy diagnosis structure; M1 adds the
cause-specific AIDS-symptom hazard with only the three MSM multiplier anchors and
the symptom probability varying. M2 is described, never built.

The focused-calibration harness (run_stage_structured_diagnosis_focused_calibration)
is used as a library and is not edited, so the completed focused run keeps its
recorded provenance.

Subcommands (one output root, one provenance chain):

  audit-evidence      static, read-only extraction; creates the output root
  validate-plan       builds every configuration (no engine); freezes the plan
  smoke               optional: 2 real trajectories, separate directory [user-run]
  benchmark           Stage 1, 150 trajectories                  [user-run]
  discover            Stage 2 shard --shard-index K --shard-count 8   [user-run]
  merge-discovery     merges shard archives with integrity checks
  freeze-shortlist    freezes at most 12 finalists before confirmation seeds
  confirm             Stage 3 shard --shard-index K --shard-count 2   [user-run]
  merge-confirmation  merges confirmation shards
  report              tables, checks, decomposition, figures, REPORT.md
  status              completed / missing / failed / duplicate pairs per unit
  resume              continues the next incomplete unit (never crosses a gate)
  run-all             sequential; stops at the shortlist gate without approval
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import io
import json
import math
import shutil
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
import run_coupled_calibration as rcc
import run_post2015_stock_flow_decomposition as ledger_module
import run_stage_structured_diagnosis_audit as audit
import run_stage_structured_diagnosis_focused_calibration as fc
from aids_observation import OBSERVED_AIDS_PROXY_COMPONENTS
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
DESIGN = "diagnosis_triangulation_orchestration_20260922"
DEFAULT_PLAN = ROOT / "configs/diagnosis_triangulation_orchestration_20260922.json"
DEFAULT_OUTPUT = ROOT / "outputs/calibration/diagnosis_triangulation_orchestration_20260922"
DEFAULT_SMOKE_OUTPUT = ROOT / "outputs/calibration/diagnosis_triangulation_orchestration_20260922_smoke"
DOC_SOURCE = ROOT / "documentation/diagnosis_triangulation_20260922"
LITERATURE = WORKSPACE / "literature"

COORDINATES = fc.FOCUSED_NAMES
SYMPTOM = fc.SYMPTOM_NAME
DX_STAGES = fc.DX_STAGES
#: Series kept before 1996: the 1985 boundary check plus the 1990/1995 historical
#: AIDS cross-validation (never scored) and early MSM diagnoses/infections.
EARLY_SERIES = tuple(fc.EARLY_SERIES) + ("aids_first_diagnosis_proxy_msm", "new_diagnoses_msm",
                                         "new_infections_msm")

STATUS_NOT_STARTED = "NOT_STARTED"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETE = "COMPLETE"
STATUS_FAILED = "FAILED"
STATUS_PARTIAL = "PARTIAL_TIME_LIMIT"
STATUS_SKIPPED_RESERVE = "NOT_STARTED_TIME_RESERVE"

LABEL_SUPPORTED = "Supported as a preliminary structural improvement"
LABEL_PROMISING = "Promising but underconstrained"
LABEL_UNSUPPORTED = "Unsupported in the tested form"
LABEL_UNRESOLVABLE = "Unresolvable with current disease states"
LABEL_INCOMPLETE = "Incomplete design (PARTIAL_TIME_LIMIT): no final classification"
DECISION_LABELS = (LABEL_SUPPORTED, LABEL_PROMISING, LABEL_UNSUPPORTED, LABEL_UNRESOLVABLE)

#: Files the implementation agent authors; audit-evidence copies them into the root.
AUTHORED_DOCUMENTS = ("AIDS_DEFINITION_ADJUDICATION.md", "IMPLEMENTATION_NOTE.md",
                      "USER_RUN_COMMANDS.md")
ADJUDICATION_SECTIONS = (
    "## 1. Operational simulator definition",
    "## 2. Source definition for Chronic-to-AIDS progression",
    "## 3. Definitions used for mortality and economic parameters",
    "## 4. Definition of every surveillance comparator",
    "## 5. Exact, approximate, bounded or unavailable comparisons",
    "## 6. Consequence for this experiment",
)


# ==========================================================================
# Small helpers: atomic, never-silently-overwriting writes
# ==========================================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path):
    return json.loads(Path(path).read_text())


def _json_text(value) -> str:
    return json.dumps(ccs.json_safe(value), indent=2, default=str) + "\n"


def write_new(path: Path, text: str | bytes) -> str:
    """Write once. Identical content is an idempotent no-op; different content refuses.

    Returns "written" or "unchanged".
    """
    path = Path(path)
    data = text.encode() if isinstance(text, str) else text
    if path.exists():
        if path.read_bytes() == data:
            return "unchanged"
        raise ContractError(f"refusing to overwrite existing file with different content: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return "written"


def write_state(path: Path, value) -> None:
    """Mutable state files (manifest, shard state, clock): atomic replace."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    rcc.atomic_write_text(Path(path), _json_text(value))


def sha_file(path: Path) -> str:
    return ccs.sha256_file(Path(path))


def sha_json(value) -> str:
    return ccs.sha256_json(ccs.json_safe(value))


# ==========================================================================
# Plan
# ==========================================================================

def load_plan(path: Path = DEFAULT_PLAN) -> dict:
    plan = _read(path)
    if plan.get("design") != DESIGN or plan.get("status") != "FROZEN":
        raise ContractError("plan is not the frozen diagnosis-triangulation design")
    if [int(x) for x in plan["backbones"]] != [1326, 1788]:
        raise ContractError("backbones must be exactly 1326 and 1788")
    if int(plan["symptom_start_year"]) != 1985 or float(plan["routine_hazard_scale"]) != 1.0:
        raise ContractError("M1 must keep the 1985 symptom boundary and routine_hazard_scale = 1")
    coords = [(c["name"], c["anchor_year"], float(c["lower"]), float(c["upper"]))
              for c in plan["coordinates"]]
    expected = [(n, *fc.EXPECTED_FOCUSED[n]) for n in COORDINATES]
    if coords != expected:
        raise ContractError(f"coordinates or ranges differ from the authorized ranges: {coords}")
    bench, disc, conf = plan["benchmark"], plan["discovery"], plan["confirmation"]
    n_bench = len(bench["tuples"]) * len(bench["seeds"])
    n_disc = len(plan["backbones"]) * int(disc["vectors_per_backbone"]) * len(disc["seeds"])
    n_conf = int(conf["max_tuples"]) * len(conf["seeds"])
    if (n_bench, n_disc, n_conf) != (150, 1920, 360):
        raise ContractError(f"stage budgets {(n_bench, n_disc, n_conf)} differ from 150/1920/360")
    if (int(bench["trajectory_budget"]), int(disc["trajectory_budget"]),
            int(conf["max_trajectory_budget"])) != (n_bench, n_disc, n_conf):
        raise ContractError("declared stage budgets differ from the computed ones")
    if int(plan["total_trajectory_budget_max"]) != n_bench + n_disc + n_conf or \
            n_bench + n_disc + n_conf != 2430:
        raise ContractError("total budget is not 2,430")
    if len(bench["seeds"]) != 30 or len(disc["seeds"]) != 5 or len(conf["seeds"]) != 30:
        raise ContractError("seed counts differ from the brief (30 / 5 / 30)")
    if int(conf["max_tuples"]) > 12 or int(conf["legacy_comparators_within_max"]) != 2 or \
            int(conf["max_mechanism_finalists"]) != int(conf["max_tuples"]) - 2:
        raise ContractError("confirmation holds at most 12 tuples including two M0 comparators")
    if int(disc["n_inner"]) + int(disc["n_outer"]) + 1 + max(len(v) for v in disc["anchors"].values()) \
            != int(disc["vectors_per_backbone"]):
        raise ContractError("identity + anchors + inner + outer must equal vectors_per_backbone")
    if not 0 < float(disc["inner_half_width_normalized"]) < float(disc["outer_half_width_normalized"]) <= 0.5:
        raise ContractError("neighbourhood half-widths must satisfy 0 < inner < outer <= 0.5")
    if int(disc["shard_count"]) != 8 or int(conf["shard_count"]) != 2:
        raise ContractError("shard counts are frozen at 8 (discovery) and 2 (confirmation)")
    flat = seed_sets(plan)
    values = [s for group in flat.values() for s in group]
    if len(values) != len(set(values)):
        raise ContractError("benchmark, discovery, confirmation and design-RNG seeds overlap")
    t = plan["time"]
    if (float(t["stop_dispatch_seconds"]), float(t["hard_abandon_seconds"]),
            float(t["total_cap_seconds"])) != (6600.0, 6900.0, 7200.0):
        raise ContractError("time caps differ from 110 min stop / 115 min abandon / 120 min total")
    if int(t["workers_max"]) != 8:
        raise ContractError("workers_max must be 8")
    return plan


def seed_sets(plan: dict) -> dict[str, list[int]]:
    return {"benchmark": [int(s) for s in plan["benchmark"]["seeds"]],
            "discovery": [int(s) for s in plan["discovery"]["seeds"]],
            "confirmation": [int(s) for s in plan["confirmation"]["seeds"]],
            "design_rng": [int(plan["discovery"]["design_rng_seed"])],
            "smoke": [int(s) for s in plan["smoke"]["seeds"]]}


def seed_preflight(plan: dict, *, plan_path: Path = DEFAULT_PLAN,
                   outputs_root: Path | None = None, configs_root: Path | None = None) -> dict:
    """Reserved seeds must not appear in any recorded manifest or plan (own design skipped)."""
    from run_joint_fit_comparison_20260917 import _seeds_from_obj  # noqa: PLC0415

    outputs_root = Path(outputs_root) if outputs_root is not None else ROOT / "outputs"
    configs_root = Path(configs_root) if configs_root is not None else ROOT / "configs"
    found: set[int] = set(rcc.DECLARED_FOREIGN_SEEDS)
    found.update(ccs.DISCOVERY_SEEDS, ccs.CONFIRMATION_SEEDS, ccs.SMOKE_SEEDS)
    found.add(ccs.PROPOSAL_RNG_SEED)
    scanned = {"manifests": 0, "plans": 0, "own_skipped": 0}
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
    own = Path(plan_path).resolve()
    for path in sorted(configs_root.glob("*.json")) if configs_root.exists() else []:
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if path.resolve() == own or (isinstance(payload, dict) and payload.get("design") == DESIGN):
            scanned["own_skipped"] += 1
            continue
        scanned["plans"] += 1
        _seeds_from_obj(payload, found)
    reserved = seed_sets(plan)
    overlap = sorted({s for v in reserved.values() for s in v} & found)
    if overlap:
        raise ContractError(f"reserved seeds collide with recorded use: {overlap}")
    return {"reserved": reserved, "overlap": overlap, "n_recorded_seeds": len(found), **scanned,
            "limitation": "only seeds recorded in outputs/**/manifest.json, configs/*.json and the "
                          "declared foreign ranges are visible to the scan"}


# ==========================================================================
# Prior focused run: the source of backbones and candidate vectors (read-only)
# ==========================================================================

@dataclass
class PriorContext:
    plan: dict                  # the prior focused frozen plan
    bases: dict                 # backbone id -> injected backbone configuration (M0)
    state: dict                 # frozen inputs, incl. the 13 targets
    design_items: dict          # prior Stage-B tuple id -> design item (vector, u, sha)


def prior_context(plan: dict) -> PriorContext:
    prior = plan["prior_focused_run"]
    out = ROOT / prior["output_dir"]
    for name, key in (("manifest.json", "manifest_sha256"), ("frozen_plan.json", "frozen_plan_sha256"),
                      ("design.json", "design_sha256"),
                      ("confirmation_summary.csv", "confirmation_summary_sha256")):
        if sha_file(out / name) != prior[key]:
            raise ContractError(f"prior focused run file {name} differs from the pinned hash")
    prior_plan = fc.load_plan(out / "frozen_plan.json")
    _, _, _, state, bases, _ = fc._source(prior_plan)
    items = {i["tuple_id"]: i for i in _read(out / "design.json")["stage_b"]}
    return PriorContext(prior_plan, bases, state, items)


def m0_config(ctx: PriorContext, backbone: int) -> dict:
    config = fc.without_stage_block(ctx.bases[int(backbone)])
    rcc.assert_stochastic_msm_config(config)
    return config


def m1_config(ctx: PriorContext, backbone: int, vector: Mapping[str, float]) -> dict:
    return fc.stage_b_config(ctx.bases[int(backbone)], vector, ctx.plan)


def backbone_vector(ctx: PriorContext, backbone: int) -> dict[str, float]:
    return fc.backbone_vector(ctx.bases[int(backbone)], ctx.plan)


def prior_candidate(ctx: PriorContext, tuple_id: str) -> tuple[dict, dict]:
    """(vector, config) of a prior Stage-B tuple, rebuilt and hash-checked against the prior design."""
    item = ctx.design_items[tuple_id]
    config = m1_config(ctx, item["backbone"], item["vector"])
    if ccs.sha256_json(config) != item["config_sha256"]:
        raise ContractError(f"{tuple_id} does not rebuild to its prior configuration hash")
    return dict(item["vector"]), config


# ==========================================================================
# Evidence: register, observation mapping, target contract, external checks
# ==========================================================================

REGISTER_COLUMNS = ("metric", "population", "period", "value", "lower", "upper", "unit", "source",
                    "source_location", "definition", "direct_or_modelled", "tier", "role",
                    "model_output", "mapping_quality", "overlap_warning", "inclusion_decision",
                    "rationale")

PRIMARY_DX_YEARS = (1996, 2000, 2004, 2010, 2015, 2019, 2024)
PRIMARY_AIDS_YEARS = (2000, 2005, 2010, 2015, 2019, 2022)
SHM_PDF = "literature/STICHTING(2025)-HIVMonitoringReport2025NL.pdf"
OPDECOUL_PDF = "literature/deCoul(2015-FactorsLateHIVPresentationsNL.pdf"

#: Literature PDFs whose content the register and adjudication cite (pinned).
LITERATURE_SHA256 = {
    "STICHTING(2025)-HIVMonitoringReport2025NL.pdf":
        "2793bf068e43f098447f072c2efe065f9e91d6db99eaac6f299103963d7b215d",
    "deCoul(2015-FactorsLateHIVPresentationsNL.pdf":
        "f6ae1253d78cdf7b6acf8bdcddf0b310647fd99f90e6166c8e52c01d2a7675e0",
    "Progression/CASCADE(2000)_HIV1SeroconversionAIDSDeathIncubation.pdf":
        "04f57b06846bf1303e3d49bb4764b0e5c216d660ccc01deb7348519808e911a4",
    "Progression/CASCADE(2003)_SurvivalDeterminantsPostHIVSeroconversion.pdf":
        "14ee1d5e5c4afd984cccd218bc3643dd34cce9c85739b27231aa0f40bba01685",
    "Progression/Glaubius(2021)_UntreatedHIVProgressionMortalitySynthesis.pdf":
        "8e0283a041b621fefde17a61f0132e6eea01c69881d7c35e4ac574bca9491369",
    "Popping(2023)_HIVCareCostsTimedDelayedPresentation.pdf":
        "83c606dd342dff524e37502abc0bdbe72894992ca123b6ffd08d9720f26b83bb",
    "Bilsen(2021)-TestingPracticesLateStage.pdf":
        "0521c862e963a6e48bc7f297781d8363815f4a7aef9a4dc347d4946cebab62c6",
    "Berdet(2021)-ReasonsLateStageDiagnosis.pdf":
        "e7226a6927e5ebd866eca40bb62dcdcbc824b3631a4f49fd7bee19e5a86bc062",
    "Jordans(2024)-ProperTestingGuidnelinesHIV.pdf":
        "e144ec5067ede57e27b21d8111ffb29f24d76ca9c5cb7973bfeb9b6aeb32a8cb",
    "Koole(2025)_PrEPBarriersMissedOpportunitiesMSM.pdf":
        "338e8ccf033fbd4a46dea1209afafe98e31d2116aca8e3f3bfb0dec59d1c61f6",
    "RIVM(2016)-STIandHIVinNL2015.pdf":
        "b5b227a96719f9199f7ef0e77a0dbd67363c11207328a7d8ca4d09e3e0aeabf4",
}


def _row(**kw) -> dict:
    row = {c: kw.get(c, "") for c in REGISTER_COLUMNS}
    unknown = set(kw) - set(REGISTER_COLUMNS)
    if unknown:
        raise ContractError(f"unknown register fields {sorted(unknown)}")
    return row


def _sheet_rows(workbook, name: str) -> list[list]:
    return [list(r) for r in workbook[name].iter_rows(values_only=True)]


def _num(value) -> float | None:
    if value is None or (isinstance(value, str) and value.strip() in ("", "(NULL)")):
        return None
    return float(value)


def parse_shm_diagnoses(rows: list[list]) -> pd.DataFrame:
    """SHM 2025 Table 1.1 sheet: header row names columns; year rows are 4-digit years."""
    header = next(r for r in rows if r and r[0] == "Year of diagnosis")
    idx = {name: i for i, name in enumerate(header) if name}
    needed = ("MSM+Trans (CALC)", "MSM reported", "MSM adjusted", "Trans men and women reported",
              "Trans men and women adjusted", "Total Adjusted \n(No <15)")
    missing = [n for n in needed if n not in idx]
    if missing:
        raise ContractError(f"SHM diagnoses sheet lacks columns {missing}")
    out = []
    for r in rows:
        if r and isinstance(r[0], (str, int)) and str(r[0]).isdigit() and len(str(r[0])) == 4:
            out.append({"year": int(r[0]), "msm_trans_calc": _num(r[idx["MSM+Trans (CALC)"]]),
                        "msm_reported": _num(r[idx["MSM reported"]]),
                        "msm_adjusted": _num(r[idx["MSM adjusted"]]),
                        "trans_reported": _num(r[idx["Trans men and women reported"]]),
                        "trans_adjusted": _num(r[idx["Trans men and women adjusted"]]),
                        "total_adjusted_no_u15": _num(r[idx["Total Adjusted \n(No <15)"]])})
    frame = pd.DataFrame(out)
    # The CALC column is adjusted MSM + adjusted trans where published, else reported.
    for r in frame.itertuples():
        msm = r.msm_adjusted if r.msm_adjusted is not None and not pd.isna(r.msm_adjusted) else r.msm_reported
        trans = r.trans_adjusted if r.trans_adjusted is not None and not pd.isna(r.trans_adjusted) \
            else r.trans_reported
        if abs(msm + trans - r.msm_trans_calc) > 1e-9:
            raise ContractError(f"SHM MSM+Trans (CALC) does not reconcile in {r.year}")
    return frame


def parse_ecdc(rows: list[list]) -> pd.DataFrame:
    header = next(r for r in rows if r and r[0] == "HealthTopic")
    t, v = header.index("Time"), header.index("NumValue")
    return pd.DataFrame([{"year": int(r[t]), "value": float(r[v])} for r in rows
                         if r and r[0] == "HIV infection and AIDS" and r[v] is not None])


def parse_unaids(rows: list[list], *, low="(All Age Low)", mid="(All Age Mid)",
                 high="(All Age High)") -> pd.DataFrame:
    header = next(r for r in rows if r and r[0] == "Indicator")
    year_i = header.index("Time Period")
    cols = {k: (header.index(c) if c in header else None) for k, c in
            (("low", low), ("mid", mid), ("high", high))}
    out = []
    for r in rows:
        if len(r) <= year_i or r[0] in (None, "Indicator") or not isinstance(r[year_i], (int, float)):
            continue
        rec = {"year": int(r[year_i])}
        for key, i in cols.items():
            rec[key] = _num(r[i]) if i is not None and i < len(r) else None
        if rec["mid"] is not None:
            out.append(rec)
    return pd.DataFrame(out)


def parse_d76(rows: list[list]) -> tuple[pd.DataFrame, pd.DataFrame]:
    annual, route = [], []
    for r in rows:
        if len(r) < 9 or r[1] is None:
            continue
        if r[1] == "RIVM_AIDS_AND_DEATHS":
            annual.append({"year": int(r[4]), "aids": float(r[6]), "aids_cumulative": float(r[7]),
                           "deaths_hiv_patients": float(r[8])})
        elif r[1] == "RIVM_AIDS_BY_ROUTE" and str(r[4]).isdigit():
            route.append({"year": int(r[4]), "msm": float(r[8])})
    annual, route = pd.DataFrame(annual), pd.DataFrame(route)
    if not annual.empty and (annual.aids.cumsum() != annual.aids_cumulative).any():
        raise ContractError("D76 annual AIDS counts do not reconcile with the cumulative column")
    return annual, route


def parse_rivm_candidate(payload: dict) -> pd.DataFrame:
    rows = []
    for r in payload["selected"]:
        if str(r["period"]).isdigit() and not r.get("pooled_or_total") and not r.get("incomplete"):
            rows.append({"year": int(r["period"]), "msm": float(r["counts"]["msm"]),
                         "source_id": r["source_id"], "source_file": r["source_file"],
                         "pdf_pages": ";".join(str(p) for p in r["pdf_pages"])})
    return pd.DataFrame(rows).sort_values("year").reset_index(drop=True)


def load_evidence_tables(plan: dict, workbook_path: Path | None = None) -> dict:
    """Read-only extraction of every workbook sheet the register uses."""
    import openpyxl  # noqa: PLC0415

    path = Path(workbook_path) if workbook_path is not None else (ROOT / plan["evidence"]["workbook"])
    if workbook_path is None and sha_file(path) != plan["evidence"]["workbook_sha256"]:
        raise ContractError("source workbook hash differs from the frozen plan")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        tables = {
            "shm": parse_shm_diagnoses(_sheet_rows(wb, "Stiching 1.1 (Diagnoses)")),
            "ecdc_aids": parse_ecdc(_sheet_rows(wb, "EDC- AIDS")),
            "ecdc_hiv": parse_ecdc(_sheet_rows(wb, "EDC-HIV (Secondary)")),
            "unaids_infections": parse_unaids(_sheet_rows(wb, "UNAIDS-HIV Infections")),
            "unaids_plhiv": parse_unaids(_sheet_rows(wb, "UNAIDS-PLHIV)")),
            "unaids_deaths": parse_unaids(_sheet_rows(wb, "UNAIDS-AIDSDeath")),
            "unaids_art": parse_unaids(_sheet_rows(wb, "UNAIDS-ART"), low="__none__",
                                       mid="TOTAL ADULTS", high="__none__"),
        }
        tables["d76_annual"], tables["d76_msm"] = parse_d76(_sheet_rows(wb, "RIVM-D76 AIDS"))
    finally:
        wb.close()
    candidate = ROOT / plan["evidence"]["rivm_newest_vetted_candidate"]
    tables["rivm_vetted_msm"] = parse_rivm_candidate(_read(candidate))
    tables["_hashes"] = {"workbook": sha_file(path), "rivm_candidate": sha_file(candidate)}
    return tables


#: Published values transcribed from stored PDFs (read in this audit; pages are PDF pages).
LITERATURE_EVIDENCE = (
    # SHM HIV Monitoring Report 2025 (ECDC HIV Platform Tool estimates: modelled, Tier 2)
    dict(metric="msm_new_infections", population="MSM (SHM category; excludes trans)", period="2010",
         value=710, lower=680, upper=745, unit="infections/year", source=SHM_PDF,
         source_location="PDF p.16, text and Figure 1.3B",
         definition="estimated infections acquired while living in NL (ECDC HIV Platform Tool back-calculation)",
         direct_or_modelled="modelled", tier="2", role="external_check", model_output="new_infections_msm",
         mapping_quality="approximate (model MSM includes trans; domestic transmission only)",
         overlap_warning="fitted to SHM diagnoses and CD4 at diagnosis; not independent of the primary diagnosis targets",
         inclusion_decision="E3 numerical interval check, descriptive (non-rejecting)",
         rationale="historical anchor for the MSM incidence level"),
    dict(metric="msm_new_infections", population="MSM (SHM category; excludes trans)", period="2020",
         value=175, lower=160, upper=190, unit="infections/year", source=SHM_PDF,
         source_location="PDF p.16, text and Figure 1.3B", definition="as above", direct_or_modelled="modelled",
         tier="2", role="external_check", model_output="new_infections_msm", mapping_quality="approximate",
         overlap_warning="same ECDC-tool fit as the other SHM infection rows",
         inclusion_decision="E2 numerical interval check, descriptive (non-rejecting)",
         rationale="COVID-era trough; diagnosis disruption also affects the back-calculation"),
    dict(metric="msm_new_infections", population="MSM (SHM category; excludes trans)", period="2024",
         value=310, lower=250, upper=365, unit="infections/year", source=SHM_PDF,
         source_location="PDF p.16, text and Figure 1.3B", definition="as above", direct_or_modelled="modelled",
         tier="2", role="external_check", model_output="new_infections_msm", mapping_quality="approximate",
         overlap_warning="same ECDC-tool fit; terminal-year estimate with wide uncertainty",
         inclusion_decision="E1 numerical interval check; decision-relevant latent check (Tier-2 dependent)",
         rationale="the brief asks whether the larger latent epidemic implied by M1 is empirically plausible"),
    dict(metric="msm_undiagnosed_stock", population="MSM (SHM category)", period="end 2010",
         value=2145, lower=2050, upper=2225, unit="persons", source=SHM_PDF,
         source_location="PDF p.41, text and Figure 1.16B",
         definition="estimated people with undiagnosed HIV (ECDC HIV Platform Tool)",
         direct_or_modelled="modelled", tier="2", role="external_check",
         model_output="msm_undiagnosed_* snapshot at the start of 2011",
         mapping_quality="approximate (year-end vs quarterly snapshot; trans in model group)",
         overlap_warning="back-calculated from the same diagnoses plus CD4 at diagnosis",
         inclusion_decision="E5 numerical interval check, descriptive (non-rejecting)",
         rationale="historical reservoir level"),
    dict(metric="msm_undiagnosed_stock", population="MSM (SHM category)", period="end 2024",
         value=805, lower=690, upper=900, unit="persons", source=SHM_PDF,
         source_location="PDF p.41, text and Figure 1.16B; repeated PDF p.47",
         definition="estimated people with undiagnosed HIV (ECDC HIV Platform Tool)",
         direct_or_modelled="modelled", tier="2", role="external_check",
         model_output="msm_undiagnosed_* snapshot at the start of 2025 (= 2024 year end)",
         mapping_quality="approximate", overlap_warning="as above",
         inclusion_decision="E4 numerical interval check; decision-relevant latent check (Tier-2 dependent)",
         rationale="the 2025 starting reservoir is the quantity M1 moves most"),
    dict(metric="msm_plhiv", population="MSM (SHM category)", period="end 2024", value=15525, lower=15415,
         upper=15625, unit="persons", source=SHM_PDF, source_location="PDF p.47, continuum of care in MSM",
         definition="estimated MSM living with HIV incl. undiagnosed", direct_or_modelled="modelled", tier="2",
         role="external_check", model_output="msm_plhiv_state at the start of 2025",
         mapping_quality="approximate (includes registered MSM who may have emigrated; model group includes trans)",
         overlap_warning="diagnosed part is a register count; undiagnosed part is the E4 estimate",
         inclusion_decision="E6 numerical interval check, descriptive (non-rejecting)",
         rationale="2025 starting state"),
    dict(metric="msm_started_art", population="MSM (SHM category)", period="end 2024", value=14174, lower="",
         upper="", unit="persons", source=SHM_PDF, source_location="PDF p.47, continuum of care in MSM",
         definition="MSM with HIV who had started ART (ever), backlog-adjusted register count",
         direct_or_modelled="direct (register, backlog-adjusted)", tier="1", role="external_check",
         model_output="msm_on_art_state at the start of 2025",
         mapping_quality="upper bound (ever started ART >= currently on ART in the model)",
         overlap_warning="same SHM register as the diagnosis targets",
         inclusion_decision="E7 one-sided upper bound, descriptive (non-rejecting)",
         rationale="the model has no loss-to-follow-up state separate from ART dropout"),
    dict(metric="msm_diagnosed_linked", population="MSM (SHM category)", period="end 2024", value=14723,
         lower="", upper="", unit="persons", source=SHM_PDF, source_location="PDF p.47",
         definition="diagnosed and linked to care", direct_or_modelled="direct (register)", tier="1",
         role="context", model_output="msm_diagnosed_state at the start of 2025",
         mapping_quality="approximate", overlap_warning="same register",
         inclusion_decision="E8 ART coverage denominator (context)", rationale="ART coverage among diagnosed"),
    # Advanced / late presentation (the thesis-facing definition is CD4 <200 or AIDS-defining illness)
    dict(metric="advanced_presentation_count", population="MSM (excl. trans)", period="2024", value=56,
         lower="", upper="", unit="diagnoses", source=SHM_PDF, source_location="PDF p.29, Figure 1.11 caption",
         definition="CD4 <200 or AIDS-defining event at diagnosis; recent infection (<12 months) takes precedence",
         direct_or_modelled="direct (register)", tier="1", role="external_check",
         model_output="hiv_diagnoses_in_aids_stage_msm, 2024",
         mapping_quality="upper bound for the model construct (clinical AIDS at diagnosis is a subset)",
         overlap_warning="subset of the 2024 SHM diagnoses target",
         inclusion_decision="E13 one-sided upper bound (with 2 trans = 58 for the model MSM+trans group)",
         rationale="the only group- and year-specific advanced-presentation count stored"),
    dict(metric="advanced_presentation_count", population="trans men and women", period="2024", value=2,
         lower="", upper="", unit="diagnoses", source=SHM_PDF, source_location="PDF p.29, Figure 1.11 caption",
         definition="as above", direct_or_modelled="direct (register)", tier="1", role="external_check",
         model_output="part of the model MSM group", mapping_quality="as above", overlap_warning="as above",
         inclusion_decision="added to E13", rationale="the model MSM group includes trans people"),
    dict(metric="advanced_presentation_fraction", population="MSM (excl. trans), stage-classifiable",
         period="2024", value=0.21, lower="", upper="", unit="proportion", source=SHM_PDF,
         source_location="PDF p.29, Figure 1.11 caption",
         definition="advanced / stage-classifiable diagnoses (denominator includes recent infections)",
         direct_or_modelled="direct (register)", tier="1", role="external_check",
         model_output="hiv_diagnoses_in_aids_stage_msm / new_diagnoses_msm, 2024",
         mapping_quality="upper bound", overlap_warning="as above",
         inclusion_decision="E13 one-sided upper bound on the fraction",
         rationale="denominator excludes stage-unavailable diagnoses"),
    dict(metric="late_presentation_count", population="MSM (excl. trans)", period="2024", value=77,
         lower="", upper="", unit="diagnoses", source=SHM_PDF, source_location="PDF p.29, Figure 1.11 caption",
         definition="CD4 <350 or AIDS-defining event (recent infection takes precedence)",
         direct_or_modelled="direct (register)", tier="1", role="context", model_output="unavailable",
         mapping_quality="unavailable (CD4 200-349 is Chronic in this model)", overlap_warning="",
         inclusion_decision="context only; excluded from the requested advanced/AIDS definition",
         rationale="brief: CD4 200-349 is Chronic"),
    dict(metric="late_presentation_count", population="MSM (excl. trans), stage-classifiable n=742",
         period="2022-2024", value=238, lower="", upper="", unit="diagnoses", source=SHM_PDF,
         source_location="PDF p.31, Table 1.2", definition="CD4 <350 or AIDS-defining event",
         direct_or_modelled="direct (register)", tier="1", role="context", model_output="unavailable",
         mapping_quality="unavailable", overlap_warning="",
         inclusion_decision="context only (the 32.1% of the previous run)", rationale="broader construct"),
    dict(metric="late_stage_composition_aids", population="all groups, late-stage n=560", period="2022-2024",
         value=208, lower="", upper="", unit="diagnoses", source=SHM_PDF, source_location="PDF p.33, Table 1.3",
         definition="AIDS-defining event at diagnosis", direct_or_modelled="direct (register)", tier="1",
         role="context", model_output="hiv_diagnoses_in_aids_stage (all groups)",
         mapping_quality="approximate (pooled over groups; not identified for MSM)", overlap_warning="",
         inclusion_decision="context: composition of advanced presentation", rationale="208 AIDS vs 183 CD4<200 without AIDS"),
    dict(metric="late_stage_composition_cd4_lt200_no_aids", population="all groups, late-stage n=560",
         period="2022-2024", value=183, lower="", upper="", unit="diagnoses", source=SHM_PDF,
         source_location="PDF p.33, Table 1.3", definition="CD4 <200 without an AIDS-defining event",
         direct_or_modelled="direct (register)", tier="1", role="context",
         model_output="unavailable (structurally unobservable in the model)", mapping_quality="unavailable",
         overlap_warning="", inclusion_decision="context: the unrepresented component",
         rationale="pooled AIDS share of advanced = 208/391 = 53%"),
    dict(metric="advanced_presentation_fraction", population="MSM in ATHENA, n=12,314", period="1996-2014 (pooled)",
         value=round(3535 / 12314, 4), lower="", upper="", unit="proportion", source=OPDECOUL_PDF,
         source_location="PDF p.4, Table 1 (3,535 advanced among 12,314 MSM)",
         definition="CD4 <200 (first CD4 within 3 months) or CDC-C event at diagnosis or entry into care; "
                    "no recent-infection precedence",
         direct_or_modelled="direct (cohort register)", tier="1", role="external_check",
         model_output="hiv_diagnoses_in_aids_stage_msm / new_diagnoses_msm, 1996-2014",
         mapping_quality="upper bound (clinical AIDS at diagnosis is a subset of advanced disease)",
         overlap_warning="same SHM register population as the diagnosis targets",
         inclusion_decision="E14 one-sided upper bound on the pooled fraction",
         rationale="the only MSM-specific long-run advanced-presentation figure stored"),
    dict(metric="aids_share_of_advanced", population="all groups, advanced n=7,331", period="1996-2014 (pooled)",
         value=0.26, lower="", upper="", unit="proportion", source=OPDECOUL_PDF, source_location="PDF p.3, results",
         definition="AIDS at diagnosis among patients presenting with advanced disease",
         direct_or_modelled="direct (cohort register)", tier="1", role="context", model_output="unavailable",
         mapping_quality="unavailable for MSM", overlap_warning="",
         inclusion_decision="context: illustrates the size of the CD4-only component (74% of advanced)",
         rationale="no conversion coefficient is fitted in this task"),
    dict(metric="first_aids_events_in_care", population="all adult PWH registered by SHM", period="2024",
         value=6.2, lower=5.2, upper=7.4, unit="per 1,000 PYFU", source=SHM_PDF,
         source_location="PDF p.180, Figure 6.1B (see ON_ART_PROGRESSION_EVIDENCE_DECISION_2026-09-16.md)",
         definition="first AIDS-defining events after entry into care", direct_or_modelled="direct (cohort)",
         tier="1", role="context", model_output="aids_progression_while_diagnosed per diagnosed Chronic PY",
         mapping_quality="approximate", overlap_warning="already used to set the on-ART progression prior",
         inclusion_decision="context only (already consumed by the 2026-09-16 decision)", rationale="avoid double use"),
    # Mechanism literature: structure, not annual hazards
    dict(metric="ever_tested_before_diagnosis", population="recently diagnosed, NL (aMASE), late n=101 / early n=143",
         period="2013-2015", value="62% (late) vs 89% (early)", lower="", upper="", unit="proportion",
         source="literature/Bilsen(2021)-TestingPracticesLateStage.pdf", source_location="PDF p.1, abstract",
         definition="late = CD4 <350 or AIDS (study definition)", direct_or_modelled="direct (survey)", tier="context",
         role="mechanism", model_output="none", mapping_quality="unavailable", overlap_warning="",
         inclusion_decision="structure only: heterogeneous testing and missed opportunities",
         rationale="no Dutch annual hazard is identified"),
    dict(metric="missed_opportunity_reasons", population="HIV-positive individuals, Amsterdam, n=14",
         period="interviews", value="qualitative", lower="", upper="", unit="",
         source="literature/Berdet(2021)-ReasonsLateStageDiagnosis.pdf", source_location="PDF p.1, abstract",
         definition="late = CD4 <350", direct_or_modelled="direct (qualitative)", tier="context", role="mechanism",
         model_output="none", mapping_quality="unavailable", overlap_warning="",
         inclusion_decision="structure only", rationale="psychosocial and health-system barriers"),
    dict(metric="indicator_condition_testing_rate", population="GP patients with indicator conditions, 3 centres",
         period="2021-2023", value=0.254, lower="", upper="", unit="proportion",
         source="literature/Jordans(2024)-ProperTestingGuidnelinesHIV.pdf", source_location="PDF p.1, abstract",
         definition="share of indicator conditions tested for HIV", direct_or_modelled="direct (study)",
         tier="context", role="mechanism", model_output="none", mapping_quality="unavailable", overlap_warning="",
         inclusion_decision="structure only: symptom-driven recognition is incomplete",
         rationale="supports a finite symptom hazard; not a national probability"),
    dict(metric="prep_barriers_missed_opportunities", population="MSM with HIV and prior PrEP, n=11",
         period="2022-2023 interviews", value="qualitative", lower="", upper="", unit="",
         source="literature/Koole(2025)_PrEPBarriersMissedOpportunitiesMSM.pdf", source_location="PDF p.1, abstract",
         definition="", direct_or_modelled="direct (qualitative)", tier="context", role="mechanism",
         model_output="none", mapping_quality="unavailable", overlap_warning="",
         inclusion_decision="structure only", rationale="testing heterogeneity among MSM"),
)


def build_register(tables: dict, plan: dict) -> pd.DataFrame:
    rows: list[dict] = []
    shm = tables["shm"]
    wb = plan["evidence"]["workbook"]
    for r in shm.itertuples():
        primary = r.year in PRIMARY_DX_YEARS
        rows.append(_row(
            metric="hiv_diagnoses", population="MSM + trans (SHM MSM+Trans CALC)", period=str(r.year),
            value=r.msm_trans_calc, unit="diagnoses/year", source=f"{wb} :: Stiching 1.1 (Diagnoses)",
            source_location="SHM Monitoring Report 2025 Table 1.1; MSM+Trans (CALC)",
            definition="new HIV diagnoses by year of diagnosis; delay-adjusted from 2017",
            direct_or_modelled="direct (register)", tier="1",
            role="primary" if primary else "visible_not_scored",
            model_output="new_diagnoses_msm", mapping_quality="exact (group mapping MSM+trans)",
            overlap_warning="ECDC HIV confirmed cases cover the same diagnoses 1990-2017",
            inclusion_decision="primary target (frozen 13-target contract)" if primary else
            "not a predeclared anchor year; reported in trajectories only",
            rationale="predeclared anchors avoid accidental extra weight on a dense series"))
        rows.append(_row(
            metric="hiv_diagnoses_total", population="all adults (no <15)", period=str(r.year),
            value=r.total_adjusted_no_u15, unit="diagnoses/year", source=f"{wb} :: Stiching 1.1 (Diagnoses)",
            source_location="Table 1.1; Total Adjusted (No <15)", definition="all new diagnoses",
            direct_or_modelled="direct (register)", tier="1", role="accounting_comparator",
            model_output="new_diagnoses (endogenous MSM + exogenous non-MSM input)",
            mapping_quality="accounting identity (non-MSM part is a model input after C-1)",
            overlap_warning="collinear with the MSM series after C-1 (register note 2026-09-15)",
            inclusion_decision="accounting comparator only; never scored",
            rationale="one constraint must not be counted twice"))
    for r in tables["ecdc_hiv"].itertuples():
        rows.append(_row(metric="hiv_diagnoses_total_ecdc", population="all", period=str(r.year), value=r.value,
                         unit="cases/year", source=f"{wb} :: EDC-HIV (Secondary)",
                         source_location="ECDC/WHO TESSy extract 2026-05-14",
                         definition="HIV confirmed reported cases", direct_or_modelled="direct (surveillance)",
                         tier="1", role="excluded_duplicate", model_output="new_diagnoses",
                         mapping_quality="approximate", overlap_warning="duplicates SHM Table 1.1 from 1996",
                         inclusion_decision="not used", rationale="second version of one surveillance process"))
    d76 = tables["d76_annual"]
    for r in tables["ecdc_aids"].itertuples():
        rows.append(_row(metric="aids_diagnoses_total_ecdc", population="all", period=str(r.year), value=r.value,
                         unit="cases/year", source=f"{wb} :: EDC- AIDS", source_location="ECDC TESSy extract",
                         definition="AIDS confirmed reported cases", direct_or_modelled="direct (surveillance)",
                         tier="1", role="excluded_conflict", model_output="new_aids_cases",
                         mapping_quality="unavailable (source conflict)",
                         overlap_warning="conflicts with RIVM D76 (1990: 48 vs 419); unresolved",
                         inclusion_decision="retained raw, not used",
                         rationale="HM_PRESENTATION_EVIDENCE_AND_COMPOUND_DESIGN_2026-09-16.md: D76 preferred"))
    for r in d76.itertuples():
        rows.append(_row(metric="aids_diagnoses_total_rivm", population="all reported groups", period=str(r.year),
                         value=r.aids, unit="diagnoses/year", source=f"{wb} :: RIVM-D76 AIDS",
                         source_location="D76 printed pp.108-109",
                         definition="AIDS diagnoses by year of AIDS diagnosis (clinical AIDS; CDC category C)",
                         direct_or_modelled="direct (surveillance)", tier="1", role="context",
                         model_output="aids_first_diagnosis_proxy (all groups)",
                         mapping_quality="approximate; non-MSM proxy structurally one-sided (C-1 enters Chronic)",
                         overlap_warning="MSM rows are the primary AIDS series",
                         inclusion_decision="context only" + ("; 2015 explicitly incomplete" if r.year == 2015 else ""),
                         rationale="non-MSM AIDS matching is out of scope (2026-09-16 decision)"))
        if r.year <= 2014:
            rows.append(_row(
                metric="deaths_among_hiv_patients", population="all reported groups", period=str(r.year),
                value=r.deaths_hiv_patients, unit="deaths/year", source=f"{wb} :: RIVM-D76 AIDS",
                source_location="D76 printed pp.108-109; CBS before 2002, SHM from 2002",
                definition="deaths among HIV patients, ALL causes (not AIDS-attributable deaths)",
                direct_or_modelled="direct (register)", tier="1",
                role="external_check" if 2010 <= r.year <= 2014 else "context",
                model_output="HIV-excess deaths among diagnosed people (all groups)",
                mapping_quality="one-sided upper bound (all-cause >= HIV-excess among diagnosed)",
                overlap_warning="UNAIDS AIDS deaths are modelled partly from vital registration",
                inclusion_decision="E12 one-sided upper bound on 2010-2014 sum" if 2010 <= r.year <= 2014
                else "context", rationale="correct labelling: not AIDS deaths"))
    vetted = tables["rivm_vetted_msm"].set_index("year")
    d76_msm = tables["d76_msm"].set_index("year")
    for year in sorted(set(vetted.index) | set(d76_msm.index)):
        in_contract = year in PRIMARY_AIDS_YEARS
        raw = float(d76_msm.loc[year, "msm"]) if year in d76_msm.index else None
        used = float(vetted.loc[year, "msm"]) if year in vetted.index else None
        discrepancy = ""
        if raw is not None and used is not None and raw != used:
            discrepancy = (f"workbook D76 value {raw:g} differs from newest vetted {used:g} "
                           f"({vetted.loc[year, 'source_id']}); later audited decision applied")
        elif raw is not None and used is None:
            discrepancy = "workbook value retained raw; excluded from the vetted candidate"
        if year in (1990, 1995):
            role, decision = "historical_cross_validation", "visible cross-validation only (never in I_max)"
        elif in_contract:
            role, decision = "primary", "primary target (frozen 13-target contract)"
        else:
            role, decision = "visible_not_scored", "not a predeclared anchor year"
        src = vetted.loc[year] if year in vetted.index else None
        rows.append(_row(
            metric="aids_diagnoses_msm", population="MSM (RIVM transmission route)", period=str(year),
            value=used if used is not None else raw, unit="diagnoses/year",
            source=(f"literature/{src.source_file}" if src is not None else f"{wb} :: RIVM-D76 AIDS"),
            source_location=(f"{src.source_id} PDF pp.{src.pdf_pages}" if src is not None else "D76 Table 6.11"),
            definition="AIDS diagnoses by year of AIDS diagnosis, MSM route (clinical AIDS)",
            direct_or_modelled="direct (surveillance)", tier="1", role=role,
            model_output="aids_first_diagnosis_proxy_msm",
            mapping_quality="approximate (proxy = diagnosed-stage progression + HIV diagnosis in AIDS)",
            overlap_warning=discrepancy or "D76/D78/D79 editions are not independent observations",
            inclusion_decision=decision,
            rationale="RIVM_AIDS_TARGET_WINDOW_AUDIT_2026-09-21.md: AIDS primary from 2000; newest vetted edition"))
    for name, metric, output, label in (
            ("unaids_infections", "new_infections_all_ages", "new_infections (all groups)", "UNAIDS-HIV Infections"),
            ("unaids_plhiv", "plhiv_all_ages", "PLHIV (all groups)", "UNAIDS-PLHIV)"),
            ("unaids_deaths", "aids_deaths_all_ages", "aids_deaths / HIV-excess deaths (all groups)",
             "UNAIDS-AIDSDeath"),
            ("unaids_art", "art_adults", "on_art_stock (all groups)", "UNAIDS-ART")):
        for r in tables[name].itertuples():
            check = {("new_infections_all_ages", 2023): "E9a", ("plhiv_all_ages", 2023): "E9",
                     ("art_adults", 2023): "E10", ("aids_deaths_all_ages", 2023): "E11"}.get((metric, r.year))
            rows.append(_row(
                metric=metric, population="all ages / adults, all groups", period=str(r.year), value=r.mid,
                lower="" if r.low is None else r.low, upper="" if r.high is None else r.high,
                unit="persons or events", source=f"{wb} :: {label}", source_location="UNAIDS estimates 2026-05-14",
                definition="Spectrum/CSAVR estimate", direct_or_modelled="modelled", tier="2",
                role="external_check" if check else "cross_validation_visible", model_output=output,
                mapping_quality="approximate; model non-MSM infections are structurally absent (C-1 enters at diagnosis)"
                if metric == "new_infections_all_ages" else "approximate",
                overlap_warning="CSAVR is fitted to the same case surveillance and vital registration; "
                                "overlaps SHM ECDC-tool estimates",
                inclusion_decision=f"{check} descriptive Tier-2 comparison (non-rejecting)" if check else
                "visible only", rationale="Tier 2 never enters a likelihood (HISTORY_MATCHING_BUILD_SPEC 4.1)"))
    for spec in LITERATURE_EVIDENCE:
        rows.append(_row(**{k: v for k, v in spec.items()}))
    frame = pd.DataFrame(rows, columns=list(REGISTER_COLUMNS))
    primary = frame[frame.role == "primary"]
    if len(primary) != 13:
        raise ContractError(f"register marks {len(primary)} primary rows; the frozen contract has 13")
    return frame


#: The seven constructs the report must never collapse, plus the external mappings.
OBSERVATION_MAPPING = (
    ("1_biological_chronic_to_aids_progression", "aids_progression_while_undiagnosed_msm + aids_progression_while_diagnosed_msm",
     "biological transition into the clinical-AIDS compartment", "model-internal (no surveillance analogue)", "unavailable"),
    ("2_hiv_diagnosis_while_in_model_aids", "hiv_diagnoses_in_aids_stage_msm",
     "endogenous HIV diagnosis while in the clinical-AIDS compartment", "subset of advanced presentation", "lower-bound component"),
    ("3_first_aids_recognition_after_earlier_hiv_diagnosis", "aids_progression_while_diagnosed_msm",
     "Chronic-to-AIDS progression while already diagnosed", "component of RIVM AIDS by year of AIDS diagnosis", "approximate"),
    ("4_aids_first_diagnosis_proxy_msm", "aids_first_diagnosis_proxy_msm = construct 2 + construct 3",
     "established RIVM AIDS proxy", "RIVM MSM AIDS diagnoses (primary 2000-2022)", "approximate (10%/20% discrepancy)"),
    ("5_observed_advanced_presentation_cd4_lt200_or_aids", "construct 2 only (CD4 <200 without AIDS is not represented)",
     "CD4 <200 or AIDS-defining illness at diagnosis (SHM 2024; Op de Coul 1996-2014)",
     "construct 2 <= observed advanced", "one-sided upper bound on the representable component"),
    ("6_observed_clinical_aids_diagnosis_by_calendar_year", "aids_first_diagnosis_proxy_msm",
     "RIVM AIDS patients by year of AIDS diagnosis", "same as construct 4", "approximate"),
    ("7_late_presentation_cd4_lt350_or_aids", "none", "CD4 <350 or AIDS-defining illness",
     "CD4 200-349 is Chronic in this model", "context only; excluded from the requested definition"),
    ("E_msm_undiagnosed_stock", "msm_undiagnosed_{acute,chronic,aids} snapshot", "ECDC HIV Platform Tool estimate",
     "Tier 2", "approximate"),
    ("E_msm_new_infections", "new_infections_msm", "ECDC HIV Platform Tool estimate", "Tier 2", "approximate"),
    ("E_msm_plhiv_and_art", "msm_plhiv_state, msm_on_art_state, msm_diagnosed_state", "SHM continuum",
     "Tier 2 (total) / Tier 1 (register parts)", "approximate / upper bound for ART"),
    ("E_deaths", "hiv_deaths_diagnosed_* (all groups)", "RIVM D76 deaths among HIV patients (all causes)",
     "model HIV-excess deaths among diagnosed <= all-cause deaths among HIV patients", "one-sided upper bound"),
)


def observation_mapping_frame() -> pd.DataFrame:
    return pd.DataFrame(OBSERVATION_MAPPING, columns=["construct", "model_output", "definition",
                                                      "relation_to_observation", "mapping_quality"])


#: Predeclared external checks. Types: interval, one_sided_upper, descriptive.
#: decision_role: latent (enters the latent-plausibility status), advanced (advanced-presentation
#: compatibility), mortality (one-sided mortality bound), none (reported only).
EXTERNAL_CHECKS = (
    dict(id="E1", metric="msm_new_infections_2024", observed=310, lower=250, upper=365, type="interval",
         tier="2", decision_role="latent"),
    dict(id="E2", metric="msm_new_infections_2020", observed=175, lower=160, upper=190, type="interval",
         tier="2", decision_role="none"),
    dict(id="E3", metric="msm_new_infections_2010", observed=710, lower=680, upper=745, type="interval",
         tier="2", decision_role="none"),
    dict(id="E4", metric="msm_undiagnosed_end2024", observed=805, lower=690, upper=900, type="interval",
         tier="2", decision_role="latent"),
    dict(id="E5", metric="msm_undiagnosed_end2010", observed=2145, lower=2050, upper=2225, type="interval",
         tier="2", decision_role="none"),
    dict(id="E6", metric="msm_plhiv_end2024", observed=15525, lower=15415, upper=15625, type="interval",
         tier="2", decision_role="none"),
    dict(id="E7", metric="msm_on_art_end2024", observed=14174, lower=None, upper=14174, type="one_sided_upper",
         tier="1", decision_role="none"),
    dict(id="E8", metric="msm_art_coverage_of_diagnosed_end2024", observed=round(14174 / 14723, 4), lower=None,
         upper=None, type="descriptive", tier="1", decision_role="none"),
    dict(id="E9", metric="plhiv_all_2023", observed=25461.26, lower=22189.29, upper=28441.28, type="interval",
         tier="2", decision_role="none"),
    dict(id="E9a", metric="new_infections_all_2023", observed=311.89, lower=110.80, upper=486.93,
         type="descriptive", tier="2", decision_role="none"),
    dict(id="E10", metric="on_art_all_2023", observed=22446.3, lower=None, upper=None, type="descriptive",
         tier="2", decision_role="none"),
    dict(id="E11", metric="aids_deaths_all_2023", observed=70.89, lower=51.42, upper=92.72, type="descriptive",
         tier="2", decision_role="none"),
    dict(id="E12", metric="hiv_deaths_diagnosed_all_2010_2014", observed=750, lower=None, upper=750,
         type="one_sided_upper", tier="1", decision_role="mortality"),
    dict(id="E13", metric="msm_dx_in_aids_2024", observed=58, lower=None, upper=58, type="one_sided_upper",
         tier="1", decision_role="advanced"),
    dict(id="E13f", metric="msm_fraction_dx_in_aids_2024", observed=0.21, lower=None, upper=0.21,
         type="one_sided_upper", tier="1", decision_role="advanced"),
    dict(id="E14", metric="msm_fraction_dx_in_aids_1996_2014", observed=round(3535 / 12314, 4), lower=None,
         upper=round(3535 / 12314, 4), type="one_sided_upper", tier="1", decision_role="advanced"),
)


def target_contract(ctx: PriorContext, plan: dict) -> dict:
    import dataclasses  # noqa: PLC0415

    targets = [dataclasses.asdict(t) for t in ctx.state["targets"]]
    keys = [t["key"] for t in targets]
    if keys != list(ccs.FROZEN_TARGET_ORDER):
        raise ContractError("frozen targets differ from the 13-target order")
    return {
        "design": DESIGN, "source": plan["target_contract"]["source"],
        "input_sha256": ctx.state["hashes"],
        "primary_targets": targets,
        "primary_discrepancy": ccs.PRIMARY_DISCREPANCY,
        "sensitivity_discrepancy": ccs.SENSITIVITY_DISCREPANCY,
        "implausibility_cutoff": float(plan["target_contract"]["implausibility_cutoff"]),
        "acceptance": "target-by-target I_max <= 3 at 10% discrepancy; 20% reported as sensitivity only",
        "groups": plan["target_contract"]["groups"],
        "historical_cross_validation": plan["target_contract"]["historical_cross_validation"],
        "incomplete_terminal_vintages": "excluded (e.g. D76 2015 = 53, D79 2023)",
        "external_checks": [dict(c) for c in EXTERNAL_CHECKS],
        "external_check_rules": {
            "interval": "tuple mean (across seeds) inside [lower, upper]; outside is reported as above/below",
            "one_sided_upper": "tuple mean <= upper; violation is directional evidence against the tuple",
            "descriptive": "residual shown; never rejecting",
            "tier_2": "never a likelihood contribution; enters decisions only through the predeclared "
                      "latent status, with the Tier-2 dependence stated",
        },
        "predeclaration_disclosure": (
            "Declared 2026-09-22 before any trajectory of this design. The implementing agent had seen "
            "the completed focused run (outputs/calibration/stage_structured_diagnosis_focused_calibration_"
            "20260922), including its 2025 undiagnosed MSM stocks (about 280-805), before choosing E4. The "
            "E1/E4 intervals are the published 95% intervals, not tuned tolerances."),
    }


def adjudication_checks(plan: dict, ctx: PriorContext) -> dict:
    """Machine-checkable facts the adjudication relies on. Freezing is refused unless all hold."""
    base = ctx.bases[1326]["transmission_model"]["disease"]
    other = ctx.bases[1788]["transmission_model"]["disease"]
    checks = {}
    for label, disease in (("1326", base), ("1788", other)):
        checks[f"progression_source_is_cascade::{label}"] = \
            "CASCADE" in str(disease["progression"]["chronic_to_aids"]["source"])
        checks[f"aids_mortality_source_is_glaubius_cd4_bands::{label}"] = \
            "Glaubius" in str(disease["mortality"]["aids"]["source"]) and \
            "3 band" in str(disease["mortality"]["aids"]["distribution"]["notes"])
    checks["proxy_components_are_diagnosed_progression_plus_dx_in_aids"] = tuple(OBSERVED_AIDS_PROXY_COMPONENTS) \
        == ("aids_progression_while_diagnosed", "hiv_diagnoses_in_aids_stage")
    report_text = (ROOT / "hta_report.py").read_text()
    checks["report_label_seam_present_aids_labelled_cd4_lt200"] = "AIDS (CD4 <200)" in report_text
    costing = _read(ROOT / "configs/hta_costing_config.json")
    very_late = costing["disease_costs"]["hiv_non_art_care_tier_very_late_year1"]
    checks["aids_stage_costs_route_to_popping_very_late"] = very_late["applies_to_stages"] == ["AIDS"]
    checks["aids_off_art_utility_is_0_70"] = float(costing["utilities"]["aids_off_art"]["value"]) == 0.70
    for rel, digest in LITERATURE_SHA256.items():
        path = LITERATURE / rel
        checks[f"pdf_pinned::{rel}"] = path.is_file() and sha_file(path) == digest
    doc = DOC_SOURCE / "AIDS_DEFINITION_ADJUDICATION.md"
    text = doc.read_text() if doc.is_file() else ""
    for heading in ADJUDICATION_SECTIONS:
        checks[f"section_present::{heading[3:]}"] = heading in text
    checks["adjudication_states_narrower_proxy"] = "narrower proxy" in text and \
        "structurally unobservable" in text
    return {"checks": checks, "passed": all(checks.values()),
            "adjudication_sha256": sha_file(doc) if doc.is_file() else None}


# ==========================================================================
# Output root: manifest and audit-evidence
# ==========================================================================

def runtime_hashes() -> dict[str, str]:
    hashes = rcc.module_hashes()
    for name in ("stage_structured_diagnosis.py", "run_stage_structured_diagnosis_audit.py",
                 "run_post2015_stock_flow_decomposition.py",
                 "run_stage_structured_diagnosis_focused_calibration.py", Path(__file__).name):
        hashes[name] = sha_file(ROOT / name)
    return hashes


def _manifest_path(root: Path) -> Path:
    return Path(root) / "manifest.json"


def load_manifest(root: Path) -> dict:
    path = _manifest_path(root)
    if not path.is_file():
        raise ContractError(f"{root} is not an initialised output root; run audit-evidence first")
    manifest = _read(path)
    if manifest.get("design") != DESIGN:
        raise ContractError("output root belongs to a different design")
    return manifest


@contextlib.contextmanager
def manifest_lock(root: Path):
    """Exclusive advisory lock (fcntl.flock) serialising every manifest read-modify-write.

    Concurrent discovery or confirmation shards each update their own unit line; without
    this lock two shards could load the same manifest and the later save would drop the
    other's update. Lock order: a unit lock (if any) is always taken before this one.
    """
    path = Path(root) / ".manifest.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def unit_lock(root: Path, unit: str):
    """Non-blocking exclusive lock per unit: the same shard cannot run in two invocations at once."""
    path = unit_dir(root, unit) / ".running.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ContractError(f"unit {unit} is already running in another invocation") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def save_manifest(root: Path, manifest: dict) -> None:
    manifest["updated_utc"] = _now()
    write_state(_manifest_path(root), manifest)
    write_status_md(root, manifest)


def audit_evidence(plan_path: Path, root: Path, *, workbook_path: Path | None = None,
                   ctx: PriorContext | None = None) -> dict:
    """Static, read-only extraction. Creates the root; idempotent when rerun on identical inputs."""
    with fc.EngineGuard():
        plan = load_plan(plan_path)
        ctx = ctx or prior_context(plan)
        tables = load_evidence_tables(plan, workbook_path)
        register = build_register(tables, plan)
        mapping = observation_mapping_frame()
        contract = target_contract(ctx, plan)
        adjudication = adjudication_checks(plan, ctx)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    written = {}
    written["empirical_constraint_register.csv"] = write_new(root / "empirical_constraint_register.csv",
                                                             register.to_csv(index=False))
    written["observation_mapping.csv"] = write_new(root / "observation_mapping.csv", mapping.to_csv(index=False))
    written["frozen_target_contract.json"] = write_new(root / "frozen_target_contract.json", _json_text(contract))
    written["adjudication_checks.json"] = write_new(root / "adjudication_checks.json", _json_text(adjudication))
    for name in AUTHORED_DOCUMENTS:
        source = DOC_SOURCE / name
        if not source.is_file():
            raise ContractError(f"authored document missing: {source}")
        written[name] = write_new(root / name, source.read_bytes())
    evidence_hashes = {name: sha_file(root / name) for name in written}
    if _manifest_path(root).exists():
        manifest = load_manifest(root)
        if manifest["evidence_sha256"] != evidence_hashes:
            raise ContractError("evidence differs from the recorded audit; refusing to overwrite")
        return {"status": "unchanged", "root": str(root), "adjudication_passed": adjudication["passed"]}
    manifest = {
        "design": DESIGN, "status": "EVIDENCE_AUDITED", "created_utc": _now(),
        "plan_sha256": sha_file(plan_path), "evidence_sha256": evidence_hashes,
        "source_hashes": {**tables["_hashes"], "prior_focused_run": plan["prior_focused_run"]},
        "adjudication_passed": adjudication["passed"],
        "plan_frozen": False, "units": {}, "shortlist_sha256": None, "gates": {},
        "environment": rcc.environment(),
        "engines_constructed": 0, "simulations_started": 0,
    }
    with manifest_lock(root):
        save_manifest(root, manifest)
    return {"status": "written", "root": str(root), "files": written,
            "register_rows": int(len(register)), "adjudication_passed": adjudication["passed"],
            "engines_constructed": 0, "simulations_started": 0}


# ==========================================================================
# Design: benchmark tuples, discovery vectors, shards
# ==========================================================================

def _u(vector: Mapping[str, float], ctx: PriorContext) -> np.ndarray:
    return fc.normalise(vector, ctx.plan)


def _box(centre: np.ndarray, half: float) -> tuple[np.ndarray, np.ndarray]:
    return np.clip(centre - half, 0.0, 1.0), np.clip(centre + half, 0.0, 1.0)


def _in_box(u: np.ndarray, lo: np.ndarray, hi: np.ndarray, tol: float = 1e-12) -> bool:
    return bool(np.all(u >= lo - tol) and np.all(u <= hi + tol))


def greedy_maximin(pool: np.ndarray, existing: np.ndarray, n: int) -> np.ndarray:
    """Farthest-point selection from ``pool`` given already placed points (deterministic)."""
    chosen = []
    dmin = np.full(len(pool), np.inf)
    for point in np.atleast_2d(existing):
        dmin = np.minimum(dmin, np.sqrt(((pool - point) ** 2).sum(axis=1)))
    for _ in range(int(n)):
        i = int(np.argmax(dmin))
        if not np.isfinite(dmin[i]) and len(chosen) == 0 and len(existing) == 0:
            i = 0
        chosen.append(i)
        dmin = np.minimum(dmin, np.sqrt(((pool - pool[i]) ** 2).sum(axis=1)))
        dmin[i] = -np.inf
    return pool[chosen]


def neighbourhood_design(centre_u: np.ndarray, fixed_u: np.ndarray, spec: dict, rng_seed: int) -> dict:
    """Inner maximin LHS box plus an outer ring, all within [0, 1]^4 (authorized ranges)."""
    inner_lo, inner_hi = _box(centre_u, float(spec["inner_half_width_normalized"]))
    outer_lo, outer_hi = _box(centre_u, float(spec["outer_half_width_normalized"]))
    d = len(centre_u)
    unit = fc.maximin_lhs(int(spec["n_inner"]), d, int(rng_seed), int(spec["maximin_lhs_candidates"]),
                          exclude=None)
    inner = inner_lo + unit * (inner_hi - inner_lo)
    rng = np.random.default_rng(int(rng_seed) + 1)
    pool = outer_lo + rng.random((int(spec["outer_pool_size"]), d)) * (outer_hi - outer_lo)
    pool = pool[[not _in_box(p, inner_lo, inner_hi) for p in pool]]
    if len(pool) < int(spec["n_outer"]):
        raise ContractError("outer-ring pool is too small")
    outer = greedy_maximin(pool, np.vstack([fixed_u, inner]), int(spec["n_outer"]))
    return {"inner": inner, "outer": outer, "inner_box": (inner_lo, inner_hi),
            "outer_box": (outer_lo, outer_hi)}


def build_design(plan: dict, ctx: PriorContext) -> dict:
    """Every benchmark and discovery tuple with its configuration. No engine."""
    tolerance = 1e-12
    benchmark = []
    for spec in plan["benchmark"]["tuples"]:
        cid = int(spec["backbone"])
        if spec["model"] == "M0":
            vector, config = backbone_vector(ctx, cid), m0_config(ctx, cid)
        else:
            vector, config = prior_candidate(ctx, spec["prior_tuple_id"])
        benchmark.append({"stage": "benchmark", "tuple_id": spec["tuple_id"], "backbone": cid,
                          "model": spec["model"], "role": spec["role"], "vector": vector,
                          "u": [float(x) for x in _u(vector, ctx)], "origin": spec.get("prior_tuple_id", "M0"),
                          "config": config})
    disc_spec = plan["discovery"]
    discovery = []
    geometry = {}
    for bi, cid in enumerate(int(c) for c in plan["backbones"]):
        centre_id = disc_spec["centres"][str(cid)]
        centre_vec, _ = prior_candidate(ctx, centre_id)
        centre_u = _u(centre_vec, ctx)
        entries = [{"tuple_id": f"D_{cid}_v000", "model": "M0", "origin": "identity",
                    "vector": backbone_vector(ctx, cid), "config": m0_config(ctx, cid), "ring": "anchor"}]
        for k, prior_id in enumerate(disc_spec["anchors"][str(cid)], start=1):
            vector, config = prior_candidate(ctx, prior_id)
            entries.append({"tuple_id": f"D_{cid}_v{k:03d}", "model": "M1", "origin": f"anchor:{prior_id}",
                            "vector": vector, "config": config, "ring": "anchor"})
        fixed_u = np.vstack([_u(e["vector"], ctx) for e in entries])
        geo = neighbourhood_design(centre_u, fixed_u, disc_spec, int(disc_spec["design_rng_seed"]) + 10 * bi)
        geometry[str(cid)] = {"centre": centre_id, "centre_u": centre_u.tolist(),
                              "inner_box": [b.tolist() for b in geo["inner_box"]],
                              "outer_box": [b.tolist() for b in geo["outer_box"]]}
        for ring, points in (("inner", geo["inner"]), ("outer", geo["outer"])):
            for u in points:
                vector = fc.denormalise(u, ctx.plan)
                entries.append({"tuple_id": f"D_{cid}_v{len(entries):03d}", "model": "M1",
                                "origin": f"maximin_{ring}", "vector": vector,
                                "config": m1_config(ctx, cid, vector), "ring": ring})
        if len(entries) != int(disc_spec["vectors_per_backbone"]):
            raise ContractError(f"backbone {cid}: {len(entries)} vectors, expected "
                                f"{disc_spec['vectors_per_backbone']}")
        slices = 4
        for index, entry in enumerate(entries):
            slice_index = 0 if entry["ring"] == "anchor" else (index % slices)
            discovery.append({"stage": "discovery", "backbone": cid, **entry,
                              "u": [float(x) for x in _u(entry["vector"], ctx)],
                              "is_identity": entry["origin"] == "identity",
                              "shard_index": 2 * slice_index + bi})
    for item in benchmark + discovery:
        item["config_sha256"] = ccs.sha256_json(item["config"])
        item["routine_path_key"] = fc.routine_path_key(item["config"], item["backbone"], tolerance) \
            if item["model"] == "M1" else f"M0:{item['backbone']}"
    return {"benchmark": benchmark, "discovery": discovery, "geometry": geometry}


def check_design(plan: dict, ctx: PriorContext, design: dict) -> dict:
    checks: dict[str, object] = {}
    profile = ".".join(fc.PROFILE_PATH)
    anchors = [".".join(fc.MSM_SCHEDULE_PATH + (c["anchor_year"],)) for c in plan["coordinates"]
               if c["anchor_year"] is not None]
    base_sched = {cid: ccs.sha256_json(fc._get(b, fc.BASE_SCHEDULE_PATH)) for cid, b in ctx.bases.items()}
    for item in design["benchmark"] + design["discovery"]:
        base = ctx.bases[item["backbone"]]
        diff = fc.changed_paths(base, item["config"])
        if item["model"] == "M0":
            if diff:
                raise ContractError(f"{item['tuple_id']}: M0 must equal the backbone exactly; differs at {diff}")
            continue
        outside = [p for p in diff if not fc._allowed(p, [profile] + anchors)]
        if outside:
            raise ContractError(f"{item['tuple_id']} changed outside the M1 coordinates: {outside}")
        if ccs.sha256_json(fc._get(item["config"], fc.BASE_SCHEDULE_PATH)) != base_sched[item["backbone"]]:
            raise ContractError(f"{item['tuple_id']} changed the base diagnosis schedule")
        rho, symptom, start = fc._profile_values(item["config"])
        if rho != 1.0 or start != 1985.0 or symptom != item["vector"][SYMPTOM]:
            raise ContractError(f"{item['tuple_id']} profile differs from rho=1, 1985, vector symptom")
        for c in plan["coordinates"]:
            value = float(item["vector"][c["name"]])
            if not float(c["lower"]) <= value <= float(c["upper"]):
                raise ContractError(f"{item['tuple_id']} {c['name']}={value} outside the authorized range")
    checks["m0_equals_backbone_exactly"] = True
    checks["m1_changes_only_authorized_coordinates"] = True
    checks["base_schedule_unchanged"] = True
    checks["all_vectors_inside_authorized_ranges"] = True
    for cid in plan["backbones"]:
        items = [i for i in design["discovery"] if i["backbone"] == int(cid)]
        shas = [i["config_sha256"] for i in items]
        if len(set(shas)) != len(shas):
            raise ContractError(f"backbone {cid}: duplicate discovery configurations")
        m1 = [i for i in items if i["model"] == "M1"]
        keys = [i["routine_path_key"] for i in m1]
        symptoms = [i["vector"][SYMPTOM] for i in m1]
        if len(set(zip(keys, symptoms))) != len(m1):
            raise ContractError(f"backbone {cid}: duplicate effective (routine path, symptom) pairs")
        geo = design["geometry"][str(cid)]
        lo, hi = np.array(geo["outer_box"][0]), np.array(geo["outer_box"][1])
        for i in items:
            if i["ring"] in ("inner", "outer") and not _in_box(np.array(i["u"]), lo, hi):
                raise ContractError(f"{i['tuple_id']} lies outside the declared outer box")
    checks["discovery_configs_distinct_per_backbone"] = True
    checks["discovery_points_inside_declared_boxes"] = True
    shards = sorted({i["shard_index"] for i in design["discovery"]})
    if shards != list(range(int(plan["discovery"]["shard_count"]))):
        raise ContractError(f"discovery shards {shards} do not cover 0..7")
    for k in (0, 1):
        ids = [i for i in design["discovery"] if i["shard_index"] == k]
        if not any(i["is_identity"] for i in ids):
            raise ContractError(f"shard {k} lacks the identity comparator")
    checks["identity_and_anchors_in_first_shards"] = True
    checks["shard_sizes"] = {str(k): sum(1 for i in design["discovery"] if i["shard_index"] == k)
                             for k in shards}
    bench_ids = {i["tuple_id"]: i["config_sha256"] for i in design["benchmark"]}
    disc_anchor = {i["origin"].split(":", 1)[1]: i["config_sha256"] for i in design["discovery"]
                   if i["origin"].startswith("anchor:")}
    for tid, sha in bench_ids.items():
        if tid in disc_anchor and disc_anchor[tid] != sha:
            raise ContractError(f"{tid}: benchmark and discovery anchor configs differ")
    checks["benchmark_candidates_equal_discovery_anchors"] = True
    return checks


def design_record(design: dict) -> dict:
    strip = lambda item: {k: v for k, v in item.items() if k != "config"}  # noqa: E731
    return {"benchmark": [strip(i) for i in design["benchmark"]],
            "discovery": [strip(i) for i in design["discovery"]], "geometry": design["geometry"]}


def trajectory_counts(plan: dict, design: dict) -> dict:
    bench = len(design["benchmark"]) * len(plan["benchmark"]["seeds"])
    shards = {}
    for i in design["discovery"]:
        shards[i["shard_index"]] = shards.get(i["shard_index"], 0) + len(plan["discovery"]["seeds"])
    conf = int(plan["confirmation"]["max_tuples"]) * len(plan["confirmation"]["seeds"])
    per = float(plan["time"]["prior_seconds_per_trajectory_at_8_workers"])
    workers = int(plan["time"]["workers_max"])
    minutes = lambda n: round(n * per / workers / 60.0, 1)  # noqa: E731
    total = bench + sum(shards.values()) + conf
    return {"benchmark": {"trajectories": bench, "approx_minutes_at_8_workers": minutes(bench)},
            "discovery_shards": {str(k): {"trajectories": v, "approx_minutes_at_8_workers": minutes(v)}
                                 for k, v in sorted(shards.items())},
            "discovery_total": sum(shards.values()),
            "confirmation_max": {"trajectories": conf, "approx_minutes_at_8_workers": minutes(conf),
                                 "per_shard": conf // int(plan["confirmation"]["shard_count"])},
            "total_max": total, "approx_total_minutes_at_8_workers": minutes(total),
            "seconds_per_trajectory_assumed": per,
            "dispatch_stop_minutes": float(plan["time"]["stop_dispatch_seconds"]) / 60.0}


def validate_plan(plan_path: Path, root: Path, *, ctx: PriorContext | None = None) -> dict:
    """Builds and checks every configuration under the engine guard, then freezes the plan."""
    root = Path(root)
    manifest = load_manifest(root)
    if manifest["plan_sha256"] != sha_file(plan_path):
        raise ContractError("plan differs from the one used by audit-evidence")
    adjudication = _read(root / "adjudication_checks.json")
    if not adjudication["passed"]:
        failing = [k for k, v in adjudication["checks"].items() if not v]
        raise ContractError(f"AIDS definition adjudication has not passed its review checks: {failing}")
    for name, digest in manifest["evidence_sha256"].items():
        if sha_file(root / name) != digest:
            raise ContractError(f"evidence file {name} changed after the audit")
    with fc.EngineGuard():
        plan = load_plan(plan_path)
        ctx = ctx or prior_context(plan)
        seeds = seed_preflight(plan, plan_path=plan_path)
        design = build_design(plan, ctx)
        checks = check_design(plan, ctx, design)
        again = build_design(plan, ctx)
        if [i["config_sha256"] for i in again["discovery"]] != [i["config_sha256"] for i in design["discovery"]]:
            raise ContractError("discovery design is not reproducible")
        checks["design_reproducible"] = True
        adjudication_now = adjudication_checks(plan, ctx)
        if adjudication_now["checks"] != adjudication["checks"]:
            raise ContractError("adjudication facts changed since the evidence audit")
    counts = trajectory_counts(plan, design)
    if counts["total_max"] != int(plan["total_trajectory_budget_max"]):
        raise ContractError("computed trajectory count differs from the declared maximum")
    record = design_record(design)
    validation = {
        "ok": True, "design": DESIGN, "validated_utc": _now(), "trajectory_counts": counts,
        "seed_preflight": seeds, "checks": checks, "design_sha256": sha_json(record),
        "benchmark_tuples": [i["tuple_id"] for i in design["benchmark"]],
        "discovery_origins": pd.Series([i["origin"].split(":")[0] for i in design["discovery"]])
        .value_counts().sort_index().to_dict(),
        "runtime_module_sha256": runtime_hashes(),
        "engines_constructed": 0, "simulations_started": 0,
    }
    frozen_text = Path(plan_path).read_text()
    written = {"frozen_plan.json": write_new(root / "frozen_plan.json", frozen_text),
               "design.json": write_new(root / "design.json", _json_text(record))}
    if (root / "validation.json").exists():
        old = _read(root / "validation.json")
        if old["design_sha256"] != validation["design_sha256"] or \
                old["runtime_module_sha256"] != validation["runtime_module_sha256"]:
            raise ContractError("validation.json exists for a different design or code; refusing to overwrite")
        written["validation.json"] = "unchanged"
    else:
        written["validation.json"] = write_new(root / "validation.json", _json_text(validation))
    if not manifest["plan_frozen"]:
        manifest.update({
            "plan_frozen": True, "status": "PLAN_FROZEN", "frozen_utc": _now(),
            "frozen_plan_sha256": sha_file(root / "frozen_plan.json"),
            "design_sha256": sha_file(root / "design.json"),
            "validation_sha256": sha_file(root / "validation.json"),
            "runtime_module_sha256": validation["runtime_module_sha256"],
            "seeds": seed_sets(plan), "trajectory_counts": counts,
            "units": initial_units(plan, design),
            "time": {k: plan["time"][k] for k in ("stop_dispatch_seconds", "hard_abandon_seconds",
                                                    "total_cap_seconds")},
        })
        with manifest_lock(root):
            save_manifest(root, manifest)
    return {**validation, "written": written}


# ==========================================================================
# Units, context loading and provenance checks for execution
# ==========================================================================

def unit_names(plan: dict) -> list[str]:
    return (["benchmark"] + [f"discovery_{k:02d}" for k in range(int(plan["discovery"]["shard_count"]))]
            + [f"confirmation_{k:02d}" for k in range(int(plan["confirmation"]["shard_count"]))])


def initial_units(plan: dict, design: dict) -> dict:
    units = {}
    for name in unit_names(plan):
        units[name] = {"status": STATUS_NOT_STARTED, "planned": None, "completed": 0, "failed": 0}
    return units


def unit_dir(root: Path, unit: str) -> Path:
    if unit == "benchmark":
        return Path(root) / "benchmark"
    stage, k = unit.rsplit("_", 1)
    count = 8 if stage == "discovery" else 2
    return Path(root) / stage / f"shard_{int(k):02d}_of_{count:02d}"


@dataclass
class Run:
    root: Path
    plan_path: Path
    plan: dict
    manifest: dict
    ctx: PriorContext
    design: dict                # full design incl. configs


def load_run(root: Path, plan_path: Path, *, check_modules: bool = True,
             ctx: PriorContext | None = None) -> Run:
    root = Path(root)
    manifest = load_manifest(root)
    if not manifest.get("plan_frozen"):
        raise ContractError("the plan is not frozen; run validate-plan first")
    if sha_file(plan_path) != manifest["plan_sha256"] or \
            sha_file(root / "frozen_plan.json") != manifest["frozen_plan_sha256"] or \
            sha_file(plan_path) != manifest["frozen_plan_sha256"]:
        raise ContractError("plan differs from the frozen plan of this run")
    if sha_file(root / "design.json") != manifest["design_sha256"]:
        raise ContractError("design.json differs from the frozen design")
    for name, digest in manifest["evidence_sha256"].items():
        if sha_file(root / name) != digest:
            raise ContractError(f"evidence file {name} changed after freezing")
    if check_modules:
        current = runtime_hashes()
        drift = sorted(n for n in set(current) | set(manifest["runtime_module_sha256"])
                       if current.get(n) != manifest["runtime_module_sha256"].get(n))
        if drift:
            raise ContractError(f"runtime code changed since the plan was frozen: {drift}")
    plan = load_plan(plan_path)
    ctx = ctx or prior_context(plan)
    design = build_design(plan, ctx)
    if sha_json(design_record(design)) != sha_json(_read(root / "design.json")):
        raise ContractError("rebuilt design differs from the frozen design")
    return Run(root, Path(plan_path), plan, manifest, ctx, design)


def unit_tasks(run: Run, unit: str) -> list[dict]:
    """(tuple, seed) tasks of one unit, each carrying the configuration."""
    plan = run.plan
    if unit == "benchmark":
        tuples, seeds = run.design["benchmark"], plan["benchmark"]["seeds"]
        return [{"tuple": t, "seed": int(s)} for t in tuples for s in seeds]
    stage, k = unit.rsplit("_", 1)
    k = int(k)
    if stage == "discovery":
        tuples = [t for t in run.design["discovery"] if t["shard_index"] == k]
        return [{"tuple": t, "seed": int(s)} for t in tuples for s in plan["discovery"]["seeds"]]
    tuples = shortlist_tuples(run)
    pairs = [{"tuple": t, "seed": int(s)} for t in tuples for s in plan["confirmation"]["seeds"]]
    return [p for i, p in enumerate(pairs) if i % int(plan["confirmation"]["shard_count"]) == k]


# ==========================================================================
# One trajectory (constructs an engine; never called by audit/validate/tests)
# ==========================================================================

def extended_snapshot(population: np.ndarray, groups, year: int) -> list[dict]:
    rows = audit._stock_snapshot(population, groups, year)
    p = np.asarray(population, dtype=float)
    msm = groups.orient_to_idx["MSM"]
    dia = groups.diagnosis_states.index("Diagnosed")
    off = groups.art_states.index("Not_on_ART")
    on = groups.art_states.index("On_ART")
    aids = groups.hiv_to_idx["AIDS"]
    extra = {
        "msm_plhiv_state": p[:, msm, :, 1:, :, :, :].sum(),
        "msm_diagnosed_state": p[:, msm, :, 1:, dia, :, :].sum(),
        "msm_diagnosed_off_art_state": p[:, msm, :, 1:, dia, off, :].sum(),
        "msm_on_art_state": p[:, msm, :, 1:, :, on, :].sum(),
        "msm_aids_stage_state": p[:, msm, :, aids, :, :, :].sum(),
        "plhiv_state": p[:, :, :, 1:, :, :, :].sum(),
    }
    return rows + [{"snapshot_year": year, "series": k, "value": float(v), "activity": "All"}
                   for k, v in extra.items()]


def install_observers(engine, first_year: int, last_year: int):
    ledger = ledger_module.install_ledger(engine, first_year, last_year)
    snapshots: dict[int, list[dict]] = {}
    mortality: dict[tuple[int, str], float] = {}
    mortality_msm: dict[tuple[int, str], float] = {}
    original_mortality = engine._step_hiv_mortality
    msm = engine.groups.orient_to_idx["MSM"]

    def mortality_step(*args, **kwargs):
        before = engine.state.population.copy()
        result = original_mortality(*args, **kwargs)
        deaths = before - engine.state.population
        year = int(float(engine._current_year))
        for di, diagnosis in ((0, "undiagnosed"), (1, "diagnosed")):
            for ai, art in ((0, "off_art"), (1, "on_art")):
                key = (year, f"hiv_deaths_{diagnosis}_{art}")
                mortality[key] = mortality.get(key, 0.0) + float(deaths[:, :, :, 1:, di, ai, :].sum())
                mortality_msm[key] = mortality_msm.get(key, 0.0) + \
                    float(deaths[:, msm, :, 1:, di, ai, :].sum())
        return result

    engine._step_hiv_mortality = mortality_step
    original_step = engine.step_simulation

    def step(step_number: int):
        year = engine.time.start_year + step_number * engine.time.years_per_step
        rounded = int(round(year))
        if abs(year - rounded) < 1e-9 and 1996 <= rounded <= 2025:
            snapshots.setdefault(rounded, extended_snapshot(engine.state.population.copy(),
                                                            engine.groups, rounded))
        return original_step(step_number)

    engine.step_simulation = step
    return ledger, snapshots, mortality, mortality_msm


def run_trajectory(config: dict, meta: dict, plan: dict) -> dict:
    from calib_extractor import extract_annual_outputs  # noqa: PLC0415
    from run_v3 import SimulationEngineV3  # noqa: PLC0415

    started = time.time()
    first, last = int(plan["ledger_first_year"]), int(plan["ledger_last_year"])
    with contextlib.redirect_stdout(io.StringIO()):
        engine = SimulationEngineV3(copy.deepcopy(config), seed=int(meta["seed"]))
        engine.output_writer.enabled = False
        ledger, snapshots, mortality, mortality_msm = install_observers(engine, first, last)
        result = engine.run(run_number=1)
    snapshots[2026] = extended_snapshot(engine.state.population.copy(), engine.groups, 2026)
    outputs = extract_annual_outputs(result, int(plan["annual_start_year"]), int(plan["annual_end_year"]))
    boundary = fc.check_trajectory_outputs(outputs, plan, config)
    ledger_rows = audit._ledger_rows(ledger, engine, first, last)
    keep_from = int(plan["full_output_start_year"])
    kept = {s: {int(y): float(v) for y, v in values.items() if int(y) >= keep_from or s in EARLY_SERIES}
            for s, values in outputs.items()}
    return {
        **meta, "annual_outputs": kept, "boundary": boundary,
        "snapshots": [row for year in sorted(snapshots) for row in snapshots[year]],
        "mortality": [{"year": y, "series": s, "value": v} for (y, s), v in sorted(mortality.items())],
        "mortality_msm": [{"year": y, "series": s, "value": v} for (y, s), v in sorted(mortality_msm.items())],
        "ledger": ledger_rows,
        "summary_sha256": ccs.sha256_json(result["summary"].to_dict(orient="list")),
        "final_state_sha256": ccs.sha256_json(result["final_state"].tolist()),
        "wall_seconds": time.time() - started, "error": "",
    }


META_KEYS = ("stage", "unit", "tuple_id", "backbone", "model", "seed", "plan_sha256", "design_sha256")


def worker(payload: dict) -> dict:
    meta = {k: payload[k] for k in META_KEYS}
    try:
        return run_trajectory(payload["config"], meta, payload["plan"])
    except Exception as exc:  # noqa: BLE001 - preserved as evidence, never substituted
        return {**meta, "error": f"{type(exc).__name__}: {exc}"}


def smoke(root: Path, plan_path: Path, smoke_dir: Path) -> dict:
    """User-run: two real trajectories through the exact worker path, outside the provenance chain.

    Checks that every metric and external check used by the report is computable.
    Constructs engines; never run by the implementing agent or the tests.
    """
    smoke_dir = Path(smoke_dir)
    if smoke_dir.exists():
        raise ContractError(f"smoke directory already exists; never overwritten: {smoke_dir}")
    run = load_run(root, plan_path)
    items = {i["tuple_id"]: i for i in run.design["benchmark"]}
    smoke_dir.mkdir(parents=True)
    rows, started = [], time.time()
    for tuple_id in run.plan["smoke"]["tuples"]:
        spec = items[tuple_id]
        meta = {"stage": "smoke", "unit": "smoke", "tuple_id": tuple_id, "backbone": spec["backbone"],
                "model": spec["model"], "seed": int(run.plan["smoke"]["seeds"][0]),
                "plan_sha256": run.manifest["frozen_plan_sha256"], "design_sha256": run.manifest["design_sha256"]}
        item = worker({**meta, "config": spec["config"], "plan": run.plan})
        write_state(smoke_dir / f"{tuple_id}.json", item)
        if item.get("error"):
            raise ContractError(f"smoke trajectory {tuple_id} failed: {item['error']}")
        metrics = trajectory_metrics(item, run.ctx.state["targets"], spec["config"])
        needed = [c["metric"] for c in EXTERNAL_CHECKS] + ["ledger_diagnoses_2015_2024", "dx2024", "aids2022"]
        missing = [m for m in needed if not np.isfinite(metrics.get(m, float("nan")))]
        if missing:
            raise ContractError(f"smoke trajectory {tuple_id} lacks metrics {missing}")
        if abs(metrics["ledger_diagnoses_2015_2024"] - metrics["cum_msm_diagnoses_2015_2024"]) > 1e-6:
            raise ContractError("ledger diagnoses differ from annual MSM diagnoses 2015-2024")
        rows.append({"tuple_id": tuple_id, "wall_seconds": item["wall_seconds"],
                     **{m: metrics[m] for m in needed}})
    result = {"design": DESIGN, "role": "smoke (outside the provenance chain)", "ok": True,
              "trajectories": len(rows), "elapsed_seconds": time.time() - started, "rows": rows,
              "estimated_total_minutes_at_8_workers": float(np.mean([r["wall_seconds"] for r in rows])) *
              int(run.plan["total_trajectory_budget_max"]) / 8 / 60}
    write_state(smoke_dir / "smoke_result.json", result)
    return result


# ==========================================================================
# Clock: union of wall intervals across invocations (concurrency-safe)
# ==========================================================================

def _clock_dir(root: Path) -> Path:
    return Path(root) / "clock"


def clock_used_seconds(root: Path) -> float:
    intervals = []
    for path in sorted(_clock_dir(root).glob("*.json")):
        record = _read(path)
        intervals.append((float(record["start"]), float(record["last"])))
    return union_length(intervals)


def union_length(intervals: Iterable[tuple[float, float]]) -> float:
    total, current = 0.0, None
    for start, end in sorted((a, b) for a, b in intervals if b > a):
        if current is None or start > current[1]:
            if current is not None:
                total += current[1] - current[0]
            current = [start, end]
        else:
            current[1] = max(current[1], end)
    if current is not None:
        total += current[1] - current[0]
    return total


class ClockRecorder:
    def __init__(self, root: Path, unit: str, wall: Callable[[], float] = time.time):
        self.path = _clock_dir(root) / f"{unit}__{uuid.uuid4().hex[:12]}.json"
        self.wall = wall
        self.start = None

    def begin(self) -> None:
        self.start = self.wall()
        write_state(self.path, {"start": self.start, "last": self.start})

    def beat(self) -> None:
        if self.start is not None:
            write_state(self.path, {"start": self.start, "last": self.wall()})


def measured_seconds_per_trajectory(root: Path, plan: dict) -> float:
    """Per-trajectory worker duration (median benchmark ``wall_seconds``), else the prior 16.3 s.

    Time for n trajectories at w workers is estimated as n * rate / w.
    """
    state_path = unit_dir(root, "benchmark") / "state.json"
    if state_path.is_file():
        rate = _read(state_path).get("measured_seconds_per_trajectory")
        if rate:
            return float(rate)
    return float(plan["time"]["prior_seconds_per_trajectory_at_8_workers"])


def confirmation_reserve_seconds(root: Path, plan: dict) -> float:
    n = int(plan["confirmation"]["max_trajectory_budget"])
    done = sum(len(load_unit_journal(root, f"confirmation_{k:02d}"))
               for k in range(int(plan["confirmation"]["shard_count"])))
    return max(n - done, 0) * measured_seconds_per_trajectory(root, plan) / \
        int(plan["time"]["workers_max"]) * float(plan["time"]["reserve_safety_factor"])


# ==========================================================================
# Journals and one unit's execution
# ==========================================================================

def _journal_path(root: Path, unit: str, tuple_id: str, seed: int) -> Path:
    return unit_dir(root, unit) / "journal" / f"{tuple_id}__seed_{int(seed)}.json"


def load_unit_journal(root: Path, unit: str) -> list[dict]:
    directory = unit_dir(root, unit) / "journal"
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))] if directory.exists() else []


def _unit_state(root: Path, unit: str) -> dict:
    path = unit_dir(root, unit) / "state.json"
    return _read(path) if path.is_file() else {"unit": unit, "status": STATUS_NOT_STARTED, "invocations": []}


def _gate_for(run: Run, unit: str, shortlist_sha256: str | None) -> None:
    units = run.manifest["units"]
    if unit.startswith("discovery_") and units["benchmark"]["status"] != STATUS_COMPLETE:
        raise ContractError("discovery requires a COMPLETE benchmark (it measures the runtime "
                            "used for the confirmation reserve)")
    if unit.startswith("confirmation_"):
        path = run.root / "frozen_shortlist.json"
        if not path.is_file() or run.manifest.get("shortlist_sha256") is None:
            raise ContractError("confirmation requires a frozen shortlist (freeze-shortlist)")
        if sha_file(path) != run.manifest["shortlist_sha256"]:
            raise ContractError("frozen_shortlist.json changed after freezing")
        if shortlist_sha256 != run.manifest["shortlist_sha256"]:
            raise ContractError("confirmation needs --shortlist-sha256 equal to the frozen shortlist hash, "
                                "recording that the shortlist was reviewed")


def run_unit(run: Run, unit: str, *, workers: int, resume: bool = False,
             shortlist_sha256: str | None = None, worker_fn: Callable = worker,
             executor_factory=ProcessPoolExecutor, clock: Callable[[], float] = time.monotonic,
             wall: Callable[[], float] = time.time) -> dict:
    if unit not in run.manifest["units"]:
        raise ContractError(f"unknown unit {unit}")
    with unit_lock(run.root, unit):
        return _run_unit(run, unit, workers=workers, resume=resume, shortlist_sha256=shortlist_sha256,
                         worker_fn=worker_fn, executor_factory=executor_factory, clock=clock, wall=wall)


def _run_unit(run: Run, unit: str, *, workers: int, resume: bool, shortlist_sha256: str | None,
              worker_fn: Callable, executor_factory, clock: Callable[[], float],
              wall: Callable[[], float]) -> dict:
    if not 1 <= int(workers) <= int(run.plan["time"]["workers_max"]):
        raise ContractError("workers must be in [1, 8]")
    _gate_for(run, unit, shortlist_sha256)
    root, plan = run.root, run.plan
    state = _unit_state(root, unit)
    if state["status"] == STATUS_COMPLETE:
        return {"unit": unit, "status": STATUS_COMPLETE, "note": "already COMPLETE; nothing dispatched"}
    if state["status"] == STATUS_FAILED:
        raise ContractError(f"{unit} FAILED; failures are preserved and never retried silently")
    done = load_unit_journal(root, unit)
    if done and not resume:
        raise ContractError(f"{unit} has {len(done)} journaled trajectories; use --resume or the resume command")
    if any(i.get("error") for i in done):
        raise ContractError(f"{unit} contains failed trajectories; refusing to continue")
    plan_sha, design_sha = run.manifest["frozen_plan_sha256"], run.manifest["design_sha256"]
    for item in done:
        if item.get("plan_sha256") != plan_sha or item.get("design_sha256") != design_sha:
            raise ContractError(f"{unit} journal holds a trajectory from a different plan or design")
    finished = {(i["tuple_id"], int(i["seed"])) for i in done}
    all_tasks = unit_tasks(run, unit)
    stage = unit.split("_")[0]
    tasks = []
    for task in all_tasks:
        t = task["tuple"]
        if (t["tuple_id"], task["seed"]) in finished:
            continue
        if ccs.sha256_json(t["config"]) != t["config_sha256"]:
            raise ContractError(f"{t['tuple_id']} config hash drifted")
        tasks.append({"stage": stage, "unit": unit, "tuple_id": t["tuple_id"], "backbone": int(t["backbone"]),
                      "model": t["model"], "seed": int(task["seed"]), "plan_sha256": plan_sha,
                      "design_sha256": design_sha, "config": t["config"], "plan": plan})
    planned = len(all_tasks)
    used = clock_used_seconds(root)
    stop = float(plan["time"]["stop_dispatch_seconds"]) - used
    hard = float(plan["time"]["hard_abandon_seconds"]) - used
    if stage == "discovery":
        reserve = confirmation_reserve_seconds(root, plan)
        stop -= reserve
        hard = min(hard, stop + (float(plan["time"]["hard_abandon_seconds"]) -
                                 float(plan["time"]["stop_dispatch_seconds"])))
        estimate = len(tasks) * measured_seconds_per_trajectory(root, plan) / int(workers)
        if tasks and not done and estimate > stop:
            state.update({"status": STATUS_SKIPPED_RESERVE, "planned": planned,
                          "reason": f"estimated {estimate:.0f} s exceeds the {stop:.0f} s left after the "
                                    f"confirmation reserve ({reserve:.0f} s)"})
            write_state(unit_dir(root, unit) / "state.json", state)
            _sync_unit(run, unit, state, 0, 0)
            return {"unit": unit, **state}
    if tasks and stop <= 0:
        state.update({"status": STATUS_PARTIAL if done else STATUS_SKIPPED_RESERVE, "planned": planned})
        write_state(unit_dir(root, unit) / "state.json", state)
        _sync_unit(run, unit, state, len(done), 0)
        raise ContractError("the dispatch stop (after any confirmation reserve) is already reached")
    recorder = ClockRecorder(root, unit, wall)
    dispatch_log = unit_dir(root, unit) / "dispatch.jsonl"
    counters = {"completed": 0, "failed": 0}
    state.update({"status": STATUS_RUNNING, "planned": planned})
    write_state(unit_dir(root, unit) / "state.json", state)

    def on_dispatch(task):
        rcc.append_journal(dispatch_log, {"event": "dispatch", "tuple_id": task["tuple_id"],
                                          "seed": task["seed"], "utc": _now()})

    lookup = {(t["tuple_id"], t["seed"]): t for t in tasks}

    def on_result(item):
        task = lookup.get((item["tuple_id"], int(item["seed"])), {})
        for key in META_KEYS:
            item.setdefault(key, task.get(key))
        write_state(_journal_path(root, unit, item["tuple_id"], item["seed"]), item)
        counters["failed" if item.get("error") else "completed"] += 1
        rcc.append_journal(dispatch_log, {"event": "result", "tuple_id": item["tuple_id"], "seed": item["seed"],
                                          "error": item.get("error", ""), "utc": _now()})
        recorder.beat()

    outcome = {"elapsed_seconds": 0.0, "not_dispatched": 0, "lost_in_flight": [], "time_stopped": False,
               "hard_stopped": False}
    if tasks:
        recorder.begin()
        outcome = fc.dispatch(tasks, workers=workers, worker_fn=worker_fn, executor_factory=executor_factory,
                              clock=clock, stop_after=stop, hard_after=hard, on_dispatch=on_dispatch,
                              on_result=on_result)
        recorder.beat()
    journal = load_unit_journal(root, unit)
    failures = [i for i in journal if i.get("error")]
    ok = [i for i in journal if not i.get("error")]
    invocation = {**outcome, "workers": int(workers), "utc": _now(), "dispatched": len(tasks)}
    state["invocations"] = state.get("invocations", []) + [invocation]
    if failures:
        state["status"] = STATUS_FAILED
        pd.DataFrame(failures)[["tuple_id", "seed", "error"]].to_csv(unit_dir(root, unit) / "failures.csv",
                                                                    index=False)
    elif len(ok) == planned:
        state["status"] = STATUS_COMPLETE
        state["completed_utc"] = _now()
    else:
        state["status"] = STATUS_PARTIAL if outcome["time_stopped"] else STATUS_RUNNING
    if unit == "benchmark" and ok:
        durations = [float(i["wall_seconds"]) for i in ok if float(i.get("wall_seconds", 0.0)) > 0.0]
        if durations:
            state["measured_seconds_per_trajectory"] = float(np.median(durations))
    write_state(unit_dir(root, unit) / "state.json", state)
    _sync_unit(run, unit, state, len(ok), len(failures))
    return {"unit": unit, "status": state["status"], "planned": planned, "completed": len(ok),
            "failed": len(failures), "last_invocation": invocation,
            "clock_used_seconds": clock_used_seconds(root)}


def _sync_unit(run: Run, unit: str, state: dict, completed: int, failed: int) -> None:
    """Units record their summary line under the manifest lock (safe for concurrent shards)."""
    with manifest_lock(run.root):
        manifest = load_manifest(run.root)
        manifest["units"][unit] = {"status": state["status"], "planned": state.get("planned"),
                                   "completed": completed, "failed": failed, "updated_utc": _now()}
        manifest["clock_used_seconds"] = clock_used_seconds(run.root)
        save_manifest(run.root, manifest)
    run.manifest = manifest


# ==========================================================================
# Summaries, scoring, external checks
# ==========================================================================

def _series(item: dict, name: str, year: int, default: float = float("nan")) -> float:
    values = item["annual_outputs"].get(name)
    if values is None:
        return default
    if year in values:
        return float(values[year])
    if str(year) in values:
        return float(values[str(year)])
    return default


def _snap(item: dict, year: int, series: str | None = None, prefix: str | None = None) -> float:
    return fc._snapshot(item, year, prefix=prefix, series=series)


def _mort(rows: Sequence[dict], years: Iterable[int], prefix: str = "hiv_deaths_") -> float:
    wanted = {int(y) for y in years}
    return float(sum(float(r["value"]) for r in rows if int(r["year"]) in wanted
                     and str(r["series"]).startswith(prefix)))


def symptom_share(item_config: dict, year: int) -> float:
    """Expected share of AIDS-stage MSM diagnoses due to the symptom hazard (competing hazards).

    Accounting attribution from the configured hazards (annual average of quarterly
    step shares), not an observed cause.
    """
    rho, symptom, start = fc._profile_values(item_config)
    if symptom <= 0.0:
        return 0.0
    shares = []
    for q in range(4):
        t = year + 0.25 * q
        if start is not None and t < start:
            shares.append(0.0)
            continue
        p = float(fc.effective_msm_routine_probability(item_config, [t])[0])
        routine = 0.0 if p <= 0 else (math.inf if p >= 1 else -math.log1p(-p))
        s = -math.log1p(-symptom) if symptom < 1 else math.inf
        shares.append(0.0 if not math.isfinite(routine + s) else s / (routine + s))
    return float(np.mean(shares))


def trajectory_metrics(item: dict, targets: Sequence, config: dict) -> dict:
    """Seed-level metrics: the focused-run metrics plus the triangulation additions."""
    row = fc.trajectory_metrics(item, targets)
    dx = lambda y: _series(item, "new_diagnoses_msm", y)  # noqa: E731
    aids_dx = lambda y: _series(item, "hiv_diagnoses_in_aids_stage_msm", y)  # noqa: E731
    stage_1996_2014 = {s: sum(_series(item, f"hiv_diagnoses_in_{s}_stage_msm", y) for y in range(1996, 2015))
                       for s in DX_STAGES}
    n96 = sum(stage_1996_2014.values())
    und = lambda start_year: _snap(item, start_year, prefix="msm_undiagnosed_")  # noqa: E731
    on_art = _snap(item, 2025, series="msm_on_art_state")
    diag = _snap(item, 2025, series="msm_diagnosed_state")
    mortality = item.get("mortality", [])
    mortality_msm = item.get("mortality_msm", [])
    row.update({
        "msm_new_infections_2010": _series(item, "new_infections_msm", 2010),
        "msm_new_infections_2020": _series(item, "new_infections_msm", 2020),
        "msm_new_infections_2024": _series(item, "new_infections_msm", 2024),
        "msm_undiagnosed_end2010": und(2011),
        "msm_undiagnosed_end2024": und(2025),
        "msm_plhiv_end2024": _snap(item, 2025, series="msm_plhiv_state"),
        "msm_on_art_end2024": on_art,
        "msm_diagnosed_end2024": diag,
        "msm_diagnosed_off_art_end2024": _snap(item, 2025, series="msm_diagnosed_off_art_state"),
        "msm_art_coverage_of_diagnosed_end2024": on_art / diag if diag else float("nan"),
        "plhiv_all_2023": _snap(item, 2024, series="plhiv_state"),
        "new_infections_all_2023": _series(item, "new_infections", 2023),
        "on_art_all_2023": _snap(item, 2024, series="on_art_stock_state"),
        "aids_deaths_all_2023": _series(item, "aids_deaths", 2023),
        "hiv_deaths_diagnosed_all_2010_2014": _mort(mortality, range(2010, 2015), "hiv_deaths_diagnosed_"),
        "msm_dx_in_aids_2024": aids_dx(2024),
        "msm_fraction_dx_in_aids_2024": aids_dx(2024) / dx(2024) if dx(2024) else float("nan"),
        "msm_fraction_dx_in_aids_1996_2014": stage_1996_2014["aids"] / n96 if n96 else float("nan"),
        "msm_hiv_deaths_2015_2024": _mort(mortality_msm, range(2015, 2025)),
        **{f"msm_hiv_deaths_{k}_2015_2024": _mort(mortality_msm, range(2015, 2025), f"hiv_deaths_{k}")
           for k in ("undiagnosed_off_art", "diagnosed_off_art", "diagnosed_on_art", "undiagnosed_on_art")},
        "msm_aids_progression_undiagnosed_2015_2024": sum(
            _series(item, "aids_progression_while_undiagnosed_msm", y) for y in range(2015, 2025)),
        "msm_aids_progression_diagnosed_2015_2024": sum(
            _series(item, "aids_progression_while_diagnosed_msm", y) for y in range(2015, 2025)),
        "msm_dx_in_aids_2015_2024": sum(aids_dx(y) for y in range(2015, 2025)),
        "msm_symptom_attributed_dx_2015_2024": sum(aids_dx(y) * symptom_share(config, y)
                                                   for y in range(2015, 2025)),
        "rivm_aids_msm_1990_proxy": _series(item, "aids_first_diagnosis_proxy_msm", 1990),
        "rivm_aids_msm_1995_proxy": _series(item, "aids_first_diagnosis_proxy_msm", 1995),
    })
    for stage in DX_STAGES:
        row[f"msm_dx_{stage}_2015_2024"] = sum(_series(item, f"hiv_diagnoses_in_{stage}_stage_msm", y)
                                               for y in range(2015, 2025))
    row.update(ledger_components(item))
    return row


def ledger_components(item: dict) -> dict:
    """Exact 2015-2024 accounting for the undiagnosed MSM stock (summed over activity).

    U_end - U_start = I - D + other, so D = U_start + I + other - U_end.
    """
    rows = [r for r in item.get("ledger", []) if r["metric"] == "undiagnosed_plhiv_msm"]
    if not rows:
        return {}
    years = sorted({int(r["year"]) for r in rows})
    start = sum(float(r["stock_start"]) for r in rows if int(r["year"]) == years[0])
    end = sum(float(r["stock_end"]) for r in rows if int(r["year"]) == years[-1])
    deltas: dict[str, float] = {}
    for r in rows:
        for key, value in r.items():
            if key.startswith("delta__"):
                deltas[key[7:]] = deltas.get(key[7:], 0.0) + float(value)
    infections = deltas.get("transmission", 0.0) + deltas.get("epidemic_seed", 0.0)
    diagnoses = -deltas.get("diagnosis", 0.0)
    other = sum(v for k, v in deltas.items() if k not in ("transmission", "epidemic_seed", "diagnosis"))
    residual = start + infections + other - end - diagnoses
    if abs(residual) > 1e-6:
        raise ContractError(f"undiagnosed MSM ledger identity fails by {residual}")
    return {"ledger_u_start_2015": start, "ledger_u_end_2024": end, "ledger_infections_2015_2024": infections,
            "ledger_diagnoses_2015_2024": diagnoses, "ledger_other_exits_2015_2024": other}


def summarise(journal: Sequence[dict], tuples: Sequence[dict], seeds: Sequence[int],
              targets: Sequence) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(per-tuple summary, target residuals, seed-level metrics) for COMPLETE tuples only."""
    by_tuple: dict[str, list[dict]] = {}
    for item in journal:
        if not item.get("error"):
            by_tuple.setdefault(item["tuple_id"], []).append(item)
    rows, residual_rows, seed_rows = [], [], []
    for spec in tuples:
        items = sorted(by_tuple.get(spec["tuple_id"], []), key=lambda i: int(i["seed"]))
        if [int(i["seed"]) for i in items] != sorted(int(s) for s in seeds):
            continue
        score, residuals = fc.score_tuple(items, targets)
        metrics = [trajectory_metrics(i, targets, spec["config"]) for i in items]
        frame = pd.DataFrame(metrics)
        for i, m in zip(items, metrics):
            seed_rows.append({"stage": spec["stage"], "tuple_id": spec["tuple_id"], "backbone": spec["backbone"],
                              "model": spec["model"], "seed": int(i["seed"]), **m})
        cross = {f"xval_{k}": float(frame[f"rivm_aids_msm_{y}_proxy"].mean())
                 for k, y in (("aids1990", 1990), ("aids1995", 1995))}
        row = {"stage": spec["stage"], "tuple_id": spec["tuple_id"], "backbone": spec["backbone"],
               "model": spec["model"], "role": spec.get("role"), "origin": spec.get("origin"),
               "ring": spec.get("ring"), "is_identity": bool(spec.get("is_identity", spec["model"] == "M0")),
               "config_sha256": spec["config_sha256"], "routine_path_key": spec["routine_path_key"],
               **score, **frame.mean(numeric_only=True).to_dict(),
               **{f"sd__{k}": v for k, v in frame.std(numeric_only=True, ddof=1).to_dict().items()},
               **{k: float(spec["vector"][k]) for k in COORDINATES},
               **{f"u_{n}": u for n, u in zip(COORDINATES, spec["u"])}, **cross}
        rows.append(row)
        for r in residuals:
            residual_rows.append({"stage": spec["stage"], "tuple_id": spec["tuple_id"],
                                  "backbone": spec["backbone"], "model": spec["model"], **r})
    return pd.DataFrame(rows), pd.DataFrame(residual_rows), pd.DataFrame(seed_rows)


def external_checks(summary: pd.DataFrame, seed_level: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tup in summary.to_dict(orient="records"):
        seeds = seed_level[seed_level.tuple_id == tup["tuple_id"]] if not seed_level.empty else seed_level
        for check in EXTERNAL_CHECKS:
            value = tup.get(check["metric"], float("nan"))
            lower, upper = check["lower"], check["upper"]
            status = "descriptive"
            if check["type"] == "interval":
                status = "inside" if lower <= value <= upper else ("above" if value > upper else "below")
            elif check["type"] == "one_sided_upper":
                status = "within_bound" if value <= upper else "violates_bound"
            if not np.isfinite(value):
                status = "unavailable"
            share = float("nan")
            if not seeds.empty and check["metric"] in seeds and check["type"] != "descriptive":
                vals = seeds[check["metric"]].to_numpy(float)
                if check["type"] == "interval":
                    share = float(np.mean((vals >= lower) & (vals <= upper)))
                else:
                    share = float(np.mean(vals <= upper))
            rows.append({"tuple_id": tup["tuple_id"], "stage": tup["stage"], "backbone": tup["backbone"],
                         "model": tup["model"], "check_id": check["id"], "metric": check["metric"],
                         "tier": check["tier"], "type": check["type"], "decision_role": check["decision_role"],
                         "observed": check["observed"], "lower": lower, "upper": upper, "model_mean": value,
                         "model_sd": tup.get(f"sd__{check['metric']}", float("nan")),
                         "residual": value - float(check["observed"]), "status": status,
                         "share_of_seeds_compatible": share})
    return pd.DataFrame(rows)


def latent_status(checks: pd.DataFrame, tuple_id: str) -> str:
    """compatible | expansion | contraction | time_to_diagnosis_mismatch | unavailable (E1, E4)."""
    c = checks[(checks.tuple_id == tuple_id) & (checks.decision_role == "latent")].set_index("check_id")
    if not {"E1", "E4"} <= set(c.index) or (c.status == "unavailable").any():
        return "unavailable"
    e1, e4 = c.loc["E1", "status"], c.loc["E4", "status"]
    if e1 == "inside" and e4 == "inside":
        return "compatible"
    if e1 == "inside":
        return "time_to_diagnosis_mismatch"
    if "above" in (e1, e4) and "below" not in (e1, e4):
        return "expansion"
    if "below" in (e1, e4) and "above" not in (e1, e4):
        return "contraction"
    return "mixed"


def advanced_status(checks: pd.DataFrame, tuple_id: str) -> str:
    c = checks[(checks.tuple_id == tuple_id) & (checks.decision_role == "advanced")]
    if c.empty or (c.status == "unavailable").all():
        return "unavailable"
    return "violated" if (c.status == "violates_bound").any() else "not_violated"


def mortality_status(checks: pd.DataFrame, tuple_id: str) -> str:
    c = checks[(checks.tuple_id == tuple_id) & (checks.decision_role == "mortality")]
    return "violated" if (c.status == "violates_bound").any() else "not_violated"


# ==========================================================================
# Paired comparison, decomposition, discovery context, identifiability
# ==========================================================================

PAIRED_METRICS = ("dx2024", "dx2024_abs_error", "aids2019", "aids2022", "cum_msm_infections_2015_2024",
                  "cum_msm_diagnoses_2015_2024", "cum_hiv_deaths_2015_2024", "msm_hiv_deaths_2015_2024",
                  "msm_undiagnosed_end2024", "msm_on_art_end2024", "msm_plhiv_end2024", "msm_dx_in_aids_2024",
                  "msm_new_infections_2024")


def paired(seed_level: pd.DataFrame, tuple_id: str, comparator_id: str) -> dict:
    from scipy import stats  # noqa: PLC0415

    a = seed_level[seed_level.tuple_id == tuple_id].set_index("seed")
    b = seed_level[seed_level.tuple_id == comparator_id].set_index("seed")
    common = sorted(set(a.index) & set(b.index))
    out = {"n_paired_seeds": len(common)}
    if len(common) < 2:
        return out
    for metric in PAIRED_METRICS:
        if metric not in a or metric not in b:
            continue
        d = (a.loc[common, metric] - b.loc[common, metric]).to_numpy(float)
        half = float(stats.t.ppf(0.975, len(d) - 1) * d.std(ddof=1) / np.sqrt(len(d)))
        out[f"paired_change__{metric}"] = float(d.mean())
        out[f"paired_ci95_low__{metric}"] = float(d.mean()) - half
        out[f"paired_ci95_high__{metric}"] = float(d.mean()) + half
    out["paired_seeds_improved_2024"] = int(np.sum(
        a.loc[common, "dx2024_abs_error"].to_numpy(float) < b.loc[common, "dx2024_abs_error"].to_numpy(float)))
    return out


def paired_seed_differences(seed_level: pd.DataFrame, pairs: Sequence[tuple[str, str]]) -> pd.DataFrame:
    """Seed-level (common random numbers) differences M1 - paired M0, preserved per seed."""
    rows = []
    for tuple_id, comparator in pairs:
        a = seed_level[seed_level.tuple_id == tuple_id].set_index("seed")
        b = seed_level[seed_level.tuple_id == comparator].set_index("seed")
        for seed in sorted(set(a.index) & set(b.index)):
            rows.append({"stage": a.loc[seed, "stage"], "tuple_id": tuple_id, "comparator": comparator,
                         "seed": int(seed), **{f"diff__{m}": float(a.loc[seed, m] - b.loc[seed, m])
                                               for m in PAIRED_METRICS if m in a and m in b}})
    return pd.DataFrame(rows)


DECOMPOSITION_TERMS = (
    ("a_entering_undiagnosed_stock_2015", "ledger_u_start_2015", +1,
     "larger undiagnosed reservoir carried into 2015 (pre-2015 history)"),
    ("b_additional_infections_2015_2024", "ledger_infections_2015_2024", +1, "additional MSM infections"),
    ("c_reservoir_left_at_2025", "ledger_u_end_2024", -1,
     "change in the reservoir left undiagnosed at the start of 2025 (timing/retention proxy)"),
    ("d_other_exits_2015_2024", "ledger_other_exits_2015_2024", +1, "deaths, ageing out, migration of undiagnosed"),
)


def decomposition(seed_level: pd.DataFrame, pairs: Sequence[tuple[str, str]]) -> pd.DataFrame:
    """Accounting decomposition of the paired change in 2015-2024 MSM diagnoses (exact identity).

    Diagnosis timing 'holding realized infections fixed' is not identifiable from saved
    draws (no counterfactual run), so only the accounting identity and labelled
    cross-cutting splits are reported.
    """
    rows = []
    for tuple_id, comparator in pairs:
        a = seed_level[seed_level.tuple_id == tuple_id].set_index("seed")
        b = seed_level[seed_level.tuple_id == comparator].set_index("seed")
        common = sorted(set(a.index) & set(b.index))
        if not common or "ledger_diagnoses_2015_2024" not in a:
            continue
        diff = lambda col: float((a.loc[common, col] - b.loc[common, col]).mean())  # noqa: E731
        total = diff("ledger_diagnoses_2015_2024")
        base = {"tuple_id": tuple_id, "comparator": comparator, "n_paired_seeds": len(common)}
        terms = 0.0
        for name, column, sign, label in DECOMPOSITION_TERMS:
            value = sign * diff(column)
            terms += value
            rows.append({**base, "component": name, "kind": "accounting_identity_term",
                         "paired_mean_change": value, "label": label})
        rows.append({**base, "component": "total_change_msm_diagnoses_2015_2024", "kind": "identity_total",
                     "paired_mean_change": total, "label": "sum of the four terms (exact)",
                     "identity_residual": total - terms})
        for stage in DX_STAGES:
            rows.append({**base, "component": f"stage_split_{stage}", "kind": "cross_cutting_split",
                         "paired_mean_change": diff(f"msm_dx_{stage}_2015_2024"),
                         "label": f"change in diagnoses made in the {stage} stage"})
        rows.append({**base, "component": "symptom_attributed_aids_stage_dx", "kind": "cross_cutting_split",
                     "paired_mean_change": diff("msm_symptom_attributed_dx_2015_2024"),
                     "label": "expected-hazard attribution of AIDS-stage diagnoses to the symptom hazard"})
        for metric, label in (("msm_on_art_end2024", "downstream: MSM on ART at 2025 start"),
                              ("msm_hiv_deaths_2015_2024", "downstream: MSM HIV deaths 2015-2024"),
                              ("msm_plhiv_end2024", "downstream: MSM PLHIV at 2025 start")):
            rows.append({**base, "component": metric, "kind": "downstream", "paired_mean_change": diff(metric),
                         "label": label})
    frame = pd.DataFrame(rows)
    if not frame.empty and "identity_residual" in frame:
        worst = frame.identity_residual.abs().max()
        if np.isfinite(worst) and worst > 1e-6:
            raise ContractError(f"decomposition identity fails by {worst}")
    return frame


def discovery_context(discovery: pd.DataFrame, plan: dict, geometry: dict) -> pd.DataFrame:
    """Per vector: same-behaviour flag, neighbourhood share, boundary flags (authorized and search box)."""
    if discovery.empty:
        return discovery
    th = plan["decision_thresholds"]
    edge = float(th["boundary_occupancy_fraction"])
    out = []
    for cid, group in discovery.groupby("backbone"):
        group = group.copy()
        ident = group[group.is_identity]
        base_err = float(ident.iloc[0].dx2024_abs_error) if not ident.empty else float("nan")
        base_inf = float(ident.iloc[0].cum_msm_infections_2015_2024) if not ident.empty else float("nan")
        group["dx2024_error_reduction_vs_identity"] = 1.0 - group.dx2024_abs_error / base_err
        group["infection_change_vs_identity"] = group.cum_msm_infections_2015_2024 / base_inf - 1.0
        group["same_qualitative_behaviour"] = group.primary_pass & (group.dx2024_abs_error < base_err) & \
            ~group.is_identity
        u = group[[f"u_{n}" for n in COORDINATES]].to_numpy(float)
        at_authorized = (u <= edge) | (u >= 1 - edge)
        lo, hi = np.array(geometry[str(int(cid))]["outer_box"][0]), np.array(geometry[str(int(cid))]["outer_box"][1])
        interior_face = lambda face, bound: np.abs(face - bound) > 1e-9  # noqa: E731
        at_search = ((np.abs(u - lo) <= edge) & interior_face(lo, 0.0)) | \
            ((np.abs(u - hi) <= edge) & interior_face(hi, 1.0))
        for j, name in enumerate(COORDINATES):
            group[f"at_authorized_bound_{name}"] = at_authorized[:, j]
            group[f"at_search_box_face_{name}"] = at_search[:, j]
        group["boundary_point"] = at_authorized.any(axis=1) | at_search.any(axis=1)
        dist = np.sqrt(((u[:, None, :] - u[None, :, :]) ** 2).sum(axis=2))
        same = group.same_qualitative_behaviour.to_numpy(bool)
        radius = float(th["neighbourhood_radius_normalized"])
        n_nb, share, outer_share = [], [], []
        rings = group.ring.to_numpy(object)
        for i in range(len(group)):
            nb = np.flatnonzero((dist[i] <= radius) & (np.arange(len(group)) != i) & ~group.is_identity.to_numpy())
            n_nb.append(len(nb))
            share.append(float(same[nb].mean()) if len(nb) else 0.0)
            onb = [k for k in nb if rings[k] == "outer"]
            outer_share.append(float(same[onb].mean()) if onb else float("nan"))
        group["n_neighbours"] = n_nb
        group["neighbour_share_same_behaviour"] = share
        group["outer_ring_neighbour_share_same_behaviour"] = outer_share
        group["non_isolated_region"] = (group.n_neighbours >= int(th["neighbourhood_min_neighbours"])) & \
            (group.neighbour_share_same_behaviour >= float(th["neighbourhood_min_share_same_behaviour"]))
        out.append(group)
    return pd.concat(out, ignore_index=True)


def identifiability(discovery: pd.DataFrame, residuals: pd.DataFrame, design: dict, plan: dict,
                    finalists: Sequence[str]) -> dict:
    if discovery.empty:
        return {"available": False}
    configs = {i["tuple_id"]: i["config"] for i in design["discovery"]}
    k = int(plan["decision_thresholds"]["identifiability_neighbours"])
    wide = residuals.pivot(index="tuple_id", columns="target", values="search_residual")
    order = list(ccs.FROZEN_TARGET_ORDER)
    out = {"available": True, "label": "exploratory local sensitivity of the 13 target means; not posterior",
           "tuples": {}, "backbones": {}}
    for cid, group in discovery.groupby("backbone"):
        group = group[~group.is_identity].reset_index(drop=True)
        y = wide.loc[group.tuple_id, order].to_numpy(float)
        eff = pd.DataFrame([fc.effective_msm_routine_probability(configs[t], [1996, 2004, 2019]).tolist()
                            for t in group.tuple_id], columns=["eff_1996", "eff_2004", "eff_2019"])
        eff[SYMPTOM] = group[SYMPTOM].to_numpy(float)
        centre_id = plan["discovery"]["centres"][str(int(cid))]
        centre_rows = group.index[group.origin == f"anchor:{centre_id}"]
        centre = int(centre_rows[0]) if len(centre_rows) else 0
        out["backbones"][str(int(cid))] = {
            "centre": centre_id,
            "raw": fc.local_sensitivity(group, list(COORDINATES), y, centre, k),
            "effective_path": fc.local_sensitivity(eff, ["eff_1996", "eff_2004", "eff_2019", SYMPTOM], y, centre, k),
        }
        for tid in finalists:
            rows = group.index[group.tuple_id == tid]
            if len(rows):
                out["tuples"][tid] = fc.local_sensitivity(group, list(COORDINATES), y, int(rows[0]), k)
    return out


def ridge_ok(sens: Mapping, plan: dict) -> bool:
    th = plan["decision_thresholds"]
    if not sens or "full_rank" not in sens:
        return False
    r2 = sens.get("symptom_column_explained_by_routine_r2")
    return bool(sens["stable"] and sens["condition_number"] <= float(th["identifiability_max_condition_number"])
                and r2 is not None and r2 <= float(th["identifiability_max_symptom_r2"]))


# ==========================================================================
# Discovery merge, shortlist, confirmation merge
# ==========================================================================

def integrity_report(run: Run, units: Sequence[str]) -> dict:
    """Completed / missing / failed / duplicate (tuple, seed) pairs across units."""
    expected, seen = {}, {}
    failed, foreign, duplicates = [], [], []
    for unit in units:
        if unit.startswith("confirmation") and not run.manifest.get("shortlist_sha256"):
            continue
        for task in unit_tasks(run, unit):
            expected[(task["tuple"]["tuple_id"], task["seed"])] = unit
        for item in load_unit_journal(run.root, unit):
            key = (item["tuple_id"], int(item["seed"]))
            if item.get("plan_sha256") != run.manifest["frozen_plan_sha256"] or \
                    item.get("design_sha256") != run.manifest["design_sha256"]:
                foreign.append({"unit": unit, "tuple_id": key[0], "seed": key[1]})
            if key in seen:
                duplicates.append({"tuple_id": key[0], "seed": key[1], "units": [seen[key], unit]})
            seen[key] = unit
            if item.get("error"):
                failed.append({"unit": unit, "tuple_id": key[0], "seed": key[1], "error": item["error"]})
            if key in expected and expected[key] != unit:
                foreign.append({"unit": unit, "tuple_id": key[0], "seed": key[1], "reason": "wrong shard"})
    missing = sorted(set(expected) - set(seen))
    return {"expected": len(expected), "completed": len(seen) - len(failed), "failed": failed,
            "missing": [{"tuple_id": t, "seed": s, "unit": expected[(t, s)]} for t, s in missing],
            "duplicates": duplicates, "foreign_or_mismatched": foreign}


def discovery_units(plan: dict) -> list[str]:
    return [f"discovery_{k:02d}" for k in range(int(plan["discovery"]["shard_count"]))]


def merge_discovery(run: Run, *, allow_partial: bool = False) -> dict:
    units = discovery_units(run.plan)
    report = integrity_report(run, units)
    if report["duplicates"] or report["foreign_or_mismatched"]:
        raise ContractError(f"refusing to merge: duplicates={report['duplicates'][:3]} "
                            f"mismatched={report['foreign_or_mismatched'][:3]}")
    if report["failed"]:
        raise ContractError(f"refusing to merge: {len(report['failed'])} failed trajectories")
    statuses = {u: _unit_state(run.root, u)["status"] for u in units}
    complete = [u for u in units if statuses[u] == STATUS_COMPLETE]
    if len(complete) != len(units):
        if not allow_partial:
            raise ContractError(f"discovery is incomplete ({statuses}); pass --allow-partial-discovery to "
                                "merge a completed prefix (labelled PARTIAL_TIME_LIMIT)")
        prefix = 0
        while prefix < len(units) and statuses[units[prefix]] == STATUS_COMPLETE:
            prefix += 1
        prefix -= prefix % 2
        if prefix < 2:
            raise ContractError("a partial discovery needs at least shards 0 and 1 complete")
        complete = units[:prefix]
    journal = [i for u in complete for i in load_unit_journal(run.root, u)]
    shard_ids = {int(u.split("_")[1]) for u in complete}
    tuples = [t for t in run.design["discovery"] if t["shard_index"] in shard_ids]
    targets = run.ctx.state["targets"]
    summary, residuals, _ = summarise(journal, tuples, run.plan["discovery"]["seeds"], targets)
    if len(summary) != len(tuples):
        raise ContractError("merged discovery lacks complete vectors in completed shards")
    summary = discovery_context(summary, run.plan, run.design["geometry"])
    for unit in complete:
        k = int(unit.split("_")[1])
        ids = {t["tuple_id"] for t in tuples if t["shard_index"] == k}
        write_new(unit_dir(run.root, unit) / "shard_summary.csv",
                  summary[summary.tuple_id.isin(ids)].to_csv(index=False))
    status = "COMPLETE" if len(complete) == len(units) else STATUS_PARTIAL
    written = {"discovery_merged.csv": write_new(run.root / "discovery_merged.csv", summary.to_csv(index=False)),
               "discovery_target_residuals.csv": write_new(run.root / "discovery_target_residuals.csv",
                                                           residuals.to_csv(index=False))}
    with manifest_lock(run.root):
        manifest = load_manifest(run.root)
        manifest["gates"]["discovery_merge"] = {"status": status, "shards": complete, "utc": _now(),
                                                "discovery_merged_sha256": sha_file(run.root / "discovery_merged.csv"),
                                                "integrity": {k: (len(v) if isinstance(v, list) else v)
                                                              for k, v in report.items()}}
        manifest["status"] = f"DISCOVERY_MERGED_{status}"
        save_manifest(run.root, manifest)
    return {"status": status, "vectors": len(summary), "shards": complete, "written": written}


def select_shortlist(discovery: pd.DataFrame, plan: dict, benchmark_ids: Sequence[str]) -> tuple[list[dict], list[dict]]:
    """Deterministic, predeclared finalist rule (at most 10 M1 tuples + 2 M0 comparators).

    Eligible: M1 discovery primary passes that are not benchmark tuples (those are
    already confirmed on 30 fresh seeds). Per backbone, in order: best 2024 error;
    lowest search J; best 2024 error among latent-compatible (E1 and E4 inside the
    published intervals on discovery means); best 2024 error with infection change
    vs identity <= +5%. Then farthest-point diversity on standardised
    (u coordinates, 2024 error reduction, infection change). Near misses only when
    passes are exhausted. Duplicate effective (routine path, symptom) are excluded.
    """
    conf = plan["confirmation"]
    max_mech = int(conf["max_mechanism_finalists"])
    entries: list[dict] = []
    exclusions: list[dict] = []
    for cid in plan["backbones"]:
        ident = discovery[(discovery.backbone == int(cid)) & discovery.is_identity]
        if ident.empty:
            raise ContractError(f"backbone {cid} identity is missing from discovery")
        entries.append({"tuple_id": ident.iloc[0].tuple_id, "backbone": int(cid), "model": "M0",
                        "role": "legacy_comparator", "selected_as": "legacy_comparator"})
    mech = discovery[~discovery.is_identity & ~discovery.origin.str.replace("anchor:", "", regex=False)
                     .isin(benchmark_ids)].copy()
    lo, hi = {}, {}
    for check in EXTERNAL_CHECKS:
        lo[check["id"]], hi[check["id"]] = check["lower"], check["upper"]
    mech["latent_compatible_discovery"] = mech.msm_new_infections_2024.between(lo["E1"], hi["E1"]) & \
        mech.msm_undiagnosed_end2024.between(lo["E4"], hi["E4"])
    passes = mech[mech.primary_pass]
    used: set[tuple[str, float]] = set()
    chosen: set[str] = set()

    def add(row, reason: str, role: str = "mechanism") -> bool:
        if sum(e["model"] == "M1" for e in entries) >= max_mech or row.tuple_id in chosen:
            return False
        key = (row.routine_path_key, round(float(row.aids_symptom_annual_probability), 12))
        if key in used:
            exclusions.append({"tuple_id": row.tuple_id, "reason": "duplicate effective path",
                               "while_selecting": reason})
            return False
        entries.append({"tuple_id": row.tuple_id, "backbone": int(row.backbone), "model": "M1", "role": role,
                        "selected_as": reason})
        chosen.add(row.tuple_id)
        used.add(key)
        return True

    def first(frame: pd.DataFrame, reason: str) -> None:
        for row in frame.itertuples(index=False):
            if add(row, reason):
                return

    for cid in plan["backbones"]:
        p = passes[passes.backbone == int(cid)]
        first(p.sort_values(["dx2024_abs_error", "search_j", "tuple_id"]), "best_2024_primary_pass")
        first(p.sort_values(["search_j", "search_mean_r", "tuple_id"]), "lowest_J_primary_pass")
        first(p[p.latent_compatible_discovery].sort_values(["dx2024_abs_error", "tuple_id"]),
              "best_2024_latent_compatible_pass")
        first(p[p.infection_change_vs_identity <= float(conf["low_infection_increase_fraction"])]
              .sort_values(["dx2024_abs_error", "tuple_id"]), "best_2024_low_infection_increase_pass")
    features = [f"u_{n}" for n in COORDINATES] + ["dx2024_error_reduction_vs_identity",
                                                  "infection_change_vs_identity"]
    while sum(e["model"] == "M1" for e in entries) < max_mech:
        remaining = passes[~passes.tuple_id.isin(chosen)]
        if remaining.empty:
            break
        pool = passes[passes.tuple_id.isin(chosen)]
        scale = passes[features].std(ddof=0).replace(0.0, 1.0)
        if pool.empty:
            best = remaining.sort_values(["search_j", "tuple_id"]).iloc[0]
        else:
            a = (remaining[features] / scale).to_numpy(float)
            b = (pool[features] / scale).to_numpy(float)
            d = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
            best = remaining.iloc[int(np.argmax(d))]
        if not add(best, "farthest_point_structural_diversity"):
            chosen.add(best.tuple_id)
    for row in mech[~mech.primary_pass].sort_values(["primary_i_max", "search_j", "tuple_id"]).itertuples(index=False):
        if sum(e["model"] == "M1" for e in entries) >= max_mech:
            break
        add(row, "near_miss_lowest_I_max", role="mechanism_near_miss")
    ids = [e["tuple_id"] for e in entries]
    if len(ids) != len(set(ids)) or len(ids) > int(conf["max_tuples"]):
        raise ContractError("shortlist is not a set of at most 12 distinct tuples")
    lookup = discovery.set_index("tuple_id")
    for entry in entries:
        row = lookup.loc[entry["tuple_id"]]
        entry.update({"config_sha256": row.config_sha256, "discovery_primary_pass": bool(row.primary_pass),
                      "discovery_primary_i_max": float(row.primary_i_max),
                      "discovery_dx2024_abs_error": float(row.dx2024_abs_error),
                      **{k: float(row[k]) for k in COORDINATES}})
    return entries, exclusions


def freeze_shortlist(run: Run) -> dict:
    gate = run.manifest["gates"].get("discovery_merge")
    if gate is None:
        raise ContractError("merge discovery first")
    path = run.root / "frozen_shortlist.json"
    if path.exists():
        return {"status": "already frozen", "shortlist_sha256": run.manifest["shortlist_sha256"]}
    if any(load_unit_journal(run.root, u) for u in unit_names(run.plan) if u.startswith("confirmation")):
        raise ContractError("confirmation trajectories exist before the shortlist")
    if sha_file(run.root / "discovery_merged.csv") != gate["discovery_merged_sha256"]:
        raise ContractError("discovery_merged.csv changed after merging")
    discovery = pd.read_csv(run.root / "discovery_merged.csv")
    bench = [i["tuple_id"] for i in run.design["benchmark"] if i["model"] == "M1"]
    entries, exclusions = select_shortlist(discovery, run.plan, bench)
    payload = {"design": DESIGN, "frozen_utc": _now(), "discovery_status": gate["status"],
               "entries": entries, "exclusions": exclusions,
               "confirmation_seeds": run.plan["confirmation"]["seeds"],
               "trajectories": len(entries) * len(run.plan["confirmation"]["seeds"]),
               "discovery_merged_sha256": gate["discovery_merged_sha256"],
               "selection_rule": select_shortlist.__doc__.strip()}
    write_new(path, _json_text(payload))
    with manifest_lock(run.root):
        manifest = load_manifest(run.root)
        manifest["shortlist_sha256"] = sha_file(path)
        manifest["status"] = "SHORTLIST_FROZEN_AWAITING_REVIEW"
        save_manifest(run.root, manifest)
    return {"status": "frozen", "shortlist": str(path), "shortlist_sha256": manifest["shortlist_sha256"],
            "n_entries": len(entries), "trajectories": payload["trajectories"]}


def shortlist_tuples(run: Run) -> list[dict]:
    path = run.root / "frozen_shortlist.json"
    if not path.is_file():
        raise ContractError("confirmation needs a frozen shortlist")
    by_id = {i["tuple_id"]: i for i in run.design["discovery"]}
    out = []
    for entry in _read(path)["entries"]:
        source = by_id[entry["tuple_id"]]
        if source["config_sha256"] != entry["config_sha256"]:
            raise ContractError(f"shortlisted {entry['tuple_id']} config differs from discovery")
        out.append({**source, "stage": "confirmation", "role": entry["role"]})
    if len(out) > int(run.plan["confirmation"]["max_tuples"]):
        raise ContractError("shortlist exceeds 12 tuples")
    return out


def confirmation_units(plan: dict) -> list[str]:
    return [f"confirmation_{k:02d}" for k in range(int(plan["confirmation"]["shard_count"]))]


def merge_confirmation(run: Run) -> dict:
    units = confirmation_units(run.plan)
    report = integrity_report(run, units)
    if report["duplicates"] or report["foreign_or_mismatched"] or report["failed"]:
        raise ContractError(f"refusing to merge confirmation: {report}")
    statuses = {u: _unit_state(run.root, u)["status"] for u in units}
    journal = [i for u in units for i in load_unit_journal(run.root, u)]
    tuples = shortlist_tuples(run)
    summary, residuals, _ = summarise(journal, tuples, run.plan["confirmation"]["seeds"], run.ctx.state["targets"])
    status = "COMPLETE" if all(s == STATUS_COMPLETE for s in statuses.values()) and \
        len(summary) == len(tuples) else STATUS_PARTIAL
    written = {"confirmation_merged.csv": write_new(run.root / "confirmation_merged.csv",
                                                    summary.to_csv(index=False))}
    with manifest_lock(run.root):
        manifest = load_manifest(run.root)
        manifest["gates"]["confirmation_merge"] = {"status": status, "utc": _now(), "units": statuses,
                                                   "confirmation_merged_sha256":
                                                       sha_file(run.root / "confirmation_merged.csv"),
                                                   "integrity": {k: (len(v) if isinstance(v, list) else v)
                                                                 for k, v in report.items()}}
        manifest["status"] = f"CONFIRMATION_MERGED_{status}"
        save_manifest(run.root, manifest)
    return {"status": status, "tuples": len(summary), "written": written}


# ==========================================================================
# Decision (pure function, unit-tested)
# ==========================================================================

CLAUSES = (
    ("c1_primary_pass_fresh_seeds", "passes the frozen 13-target Tier-1 contract on fresh seeds"),
    ("c2_material_2024_improvement", "improves the 2024 diagnosis gap materially vs the paired M0"),
    ("c3_earlier_diagnosis_acceptable", "retains an acceptable historical diagnosis trajectory"),
    ("c4_no_mortality_or_integrity_pathology", "no one-sided mortality-bound violation or negative stock"),
    ("c5_latent_compatible", "MSM infections 2024 and undiagnosed end-2024 inside SHM (Tier-2) intervals"),
    ("c6_advanced_presentation_not_violated", "representable advanced-presentation component within bounds"),
    ("c7_non_isolated_region", "non-isolated discovery neighbourhood with the same behaviour"),
    ("c8_not_boundary_dependent", "no coordinate at an authorized bound or interior search-box face"),
    ("c9_not_ridge_identified", "local sensitivity full rank, condition <= threshold, symptom distinguishable"),
)

NEXT_STEPS = {
    "incomplete": "Complete the frozen design (resume the remaining units within a newly declared time budget).",
    "no_robust_improvement": "Stop pursuing M1 in this form; report M0 with the diagnosis-timing limitation.",
    "expansion": "Constrain the MSM incidence and undiagnosed reservoir directly (e.g. add the SHM ECDC-tool "
                 "undiagnosed estimate as a declared Tier-2 validation band) before any further diagnosis tuning.",
    "c5_latent_compatible": "Obtain an independent MSM undiagnosed/incidence estimate (or the ECDC-tool inputs) "
                            "so the hidden-epidemic magnitude can be checked without Tier-2 dependence.",
    "c6_advanced_presentation_not_violated": "Obtain MSM-specific AIDS-at-diagnosis counts (SHM data request) "
                                             "to replace the one-sided advanced-presentation bound.",
    "c7_non_isolated_region": "A confirmation of the leading tuple's immediate neighbours on fresh seeds.",
    "c8_not_boundary_dependent": "Extend the local search beyond the binding face inside the authorized ranges.",
    "c9_not_ridge_identified": "Fix one of the correlated routine anchors from external testing evidence and "
                               "re-examine the symptom effect.",
    "time_to_diagnosis_mismatch": "A structural extension with time since infection (or CD4 strata) before "
                                  "M1 can be judged; do not build it in this task.",
}


LATENT_FIELDS = (("E1", "msm_new_infections_2024"), ("E4", "msm_undiagnosed_end2024"))


def latent_values(checks: pd.DataFrame, tuple_id: str) -> dict:
    """Model means and Tier-2 intervals for E1/E4, flattened for the evaluation record."""
    c = checks[(checks.tuple_id == tuple_id) & (checks.decision_role == "latent")].set_index("check_id")
    out = {}
    for cid, _ in LATENT_FIELDS:
        row = c.loc[cid] if cid in c.index else None
        for field in ("model_mean", "lower", "upper", "status"):
            out[f"{cid}_{field}"] = None if row is None else row[field]
    return out


def _latent_detail(e: Mapping) -> dict:
    detail = {}
    for cid, metric in LATENT_FIELDS:
        mean, upper = e.get(f"{cid}_model_mean"), e.get(f"{cid}_upper")
        excess = (float(mean) - float(upper)) if mean is not None and upper is not None else None
        detail[cid] = {"metric": metric, "model_mean": mean, "tier2_lower": e.get(f"{cid}_lower"),
                       "tier2_upper": upper, "status": e.get(f"{cid}_status"), "excess_over_upper": excess,
                       "excess_fraction_of_upper": (excess / float(upper)) if excess is not None and upper else None}
    return detail


def _latent_text(e: Mapping) -> str:
    parts = []
    for cid, d in _latent_detail(e).items():
        if d["model_mean"] is None:
            parts.append(f"{cid} {d['metric']} unavailable")
            continue
        parts.append(f"{cid} {d['metric']} {float(d['model_mean']):.0f} vs {d['tier2_lower']}-{d['tier2_upper']} "
                     f"({d['status']}" + (f", +{100 * d['excess_fraction_of_upper']:.0f}% over upper"
                                          if d["excess_over_upper"] is not None and d["excess_over_upper"] > 0
                                          else "") + ")")
    return "; ".join(parts)


def classify(evaluations: Sequence[Mapping], *, design_complete: bool) -> dict:
    """Exactly one label, ordered rules R0-R6 (see IMPLEMENTATION_NOTE.md)."""
    keys = [k for k, _ in CLAUSES]
    if not design_complete:
        provisional = classify(evaluations, design_complete=True)["label"] if evaluations else None
        return {"label": LABEL_INCOMPLETE, "provisional_label": provisional, "tuple_id": None,
                "reason": "the frozen design did not complete; 'unsupported' cannot be established",
                "next_step": NEXT_STEPS["incomplete"], "rule": "R0"}
    robust = [e for e in evaluations if all(bool(e[k]) for k in keys[:4])]
    if not robust:
        return {"label": LABEL_UNSUPPORTED, "tuple_id": None, "rule": "R1",
                "reason": "no fresh-seed M1 tuple combines the primary pass, a material 2024 improvement, an "
                          "acceptable historical trajectory and no mortality pathology",
                "next_step": NEXT_STEPS["no_robust_improvement"]}
    rank = lambda e: (-sum(bool(e[k]) for k in keys), e.get("primary_i_max", math.inf), e["tuple_id"])  # noqa: E731
    if all(e["latent_status"] == "expansion" for e in robust):
        # Tier-2 (model-derived, partly fitted to the same diagnoses) is a plausibility check, never
        # grounds for rejection: an expansion-only improvement is Promising with the expansion reported.
        candidates = [e for e in robust if e["c6_advanced_presentation_not_violated"]]
        if candidates:
            leader = sorted(candidates, key=rank)[0]
            failing = [k for k in keys if not leader[k]]
            return {"label": LABEL_PROMISING, "tuple_id": leader["tuple_id"], "rule": "R2",
                    "tier2_expansion": True, "latent_expansion": _latent_detail(leader),
                    "reason": "robust Tier-1 improvement, but every robust tuple places MSM incidence and/or the "
                              "undiagnosed reservoir above the SHM ECDC-tool (Tier-2) intervals ("
                              + _latent_text(leader) + "); these model-derived estimates are plausibility "
                              "checks, not grounds for rejection, so the expansion is reported rather than "
                              "rejected; failing clauses: " + ", ".join(failing),
                    "failing_clauses": failing, "next_step": NEXT_STEPS["expansion"]}
    full = [e for e in robust if all(bool(e[k]) for k in keys)]
    if full:
        leader = sorted(full, key=rank)[0]
        return {"label": LABEL_SUPPORTED, "tuple_id": leader["tuple_id"], "rule": "R3",
                "reason": "all nine clauses hold for this fresh-seed tuple (latent checks are Tier-2 dependent)",
                "next_step": "Adopt M1 in the preliminary structural specification with its documented "
                             "limitations; no further retuning."}
    lat_adv = [e for e in robust if e["c5_latent_compatible"] and e["c6_advanced_presentation_not_violated"]]
    if lat_adv:
        leader = sorted(lat_adv, key=rank)[0]
        failing = [k for k in keys if not leader[k]]
        return {"label": LABEL_PROMISING, "tuple_id": leader["tuple_id"], "rule": "R4",
                "reason": "robust and latent-compatible, but the region fails: " + ", ".join(failing),
                "failing_clauses": failing, "next_step": NEXT_STEPS[failing[0]]}
    mismatch = [e for e in robust if e["latent_status"] == "time_to_diagnosis_mismatch" and
                e["c6_advanced_presentation_not_violated"]]
    if mismatch:
        leader = sorted(mismatch, key=rank)[0]
        return {"label": LABEL_UNRESOLVABLE, "tuple_id": leader["tuple_id"], "rule": "R5",
                "reason": "incidence matches the Tier-2 estimate but the undiagnosed reservoir does not (a Tier-2-dependent diagnostic label, not a rejection): the "
                          "discriminating construct is the time-since-infection / CD4-at-diagnosis distribution, "
                          "which three disease stages cannot represent",
                "next_step": NEXT_STEPS["time_to_diagnosis_mismatch"]}
    candidates = [e for e in robust if e["c6_advanced_presentation_not_violated"]]
    if candidates:
        leader = sorted(candidates, key=rank)[0]
        failing = [k for k in keys if not leader[k]]
        out = {"label": LABEL_PROMISING, "tuple_id": leader["tuple_id"], "rule": "R6",
               "reason": "robust Tier-1 improvement, but: " + ", ".join(failing), "failing_clauses": failing,
               "next_step": NEXT_STEPS[failing[0]] if failing[0] in NEXT_STEPS else NEXT_STEPS["c5_latent_compatible"]}
        if leader["latent_status"] == "expansion":
            out.update({"tier2_expansion": True, "latent_expansion": _latent_detail(leader),
                        "reason": out["reason"] + "; Tier-2 expansion reported, not rejected (" +
                        _latent_text(leader) + ")"})
        return out
    return {"label": LABEL_UNSUPPORTED, "tuple_id": None, "rule": "R6",
            "reason": "every robust improvement violates the one-sided Tier-1 advanced-presentation bound "
                      "(SHM 2024 / Op de Coul observed counts)",
            "next_step": NEXT_STEPS["c6_advanced_presentation_not_violated"]}


def evaluate(row: Mapping, comparator: Mapping, pair: Mapping, checks: pd.DataFrame,
             disc_row: Mapping | None, sens: Mapping | None, plan: dict, residuals: pd.DataFrame,
             n_seeds: int) -> dict:
    th = plan["decision_thresholds"]
    reduction = 1.0 - row["dx2024_abs_error"] / comparator["dx2024_abs_error"] \
        if comparator["dx2024_abs_error"] > 0 else 0.0
    earlier = [f"shm_new_diagnoses_msm@{y}" for y in (1996, 2000, 2004, 2010, 2015)]
    r = residuals[(residuals.tuple_id == row["tuple_id"]) & (residuals.stage == row["stage"]) &
                  residuals.target.isin(earlier)]
    within = bool(len(r) == 5 and (r.primary_implausibility <= float(plan["target_contract"]["implausibility_cutoff"])).all())
    stocks = [row.get(k, 0.0) for k in ("msm_undiagnosed_end2024", "msm_on_art_end2024",
                                        "msm_diagnosed_off_art_end2024")]
    latent = latent_status(checks, row["tuple_id"])
    clauses = {
        "c1_primary_pass_fresh_seeds": bool(row["primary_pass"]),
        "c2_material_2024_improvement": bool(
            reduction >= float(th["material_2024_error_reduction_fraction"]) and
            pair.get("paired_seeds_improved_2024", 0) >= math.ceil(
                float(th["min_improved_paired_seeds_2024_fraction"]) * n_seeds)),
        "c3_earlier_diagnosis_acceptable": bool(
            row["R_diagnoses_1996_2015"] <= float(th["earlier_diagnosis_group_rms_tolerance"]) *
            comparator["R_diagnoses_1996_2015"] and within),
        "c4_no_mortality_or_integrity_pathology": bool(mortality_status(checks, row["tuple_id"]) == "not_violated"
                                                       and min(stocks) >= 0),
        "c5_latent_compatible": latent == "compatible",
        "c6_advanced_presentation_not_violated": advanced_status(checks, row["tuple_id"]) == "not_violated",
        "c7_non_isolated_region": bool(disc_row is not None and bool(disc_row["non_isolated_region"])),
        "c8_not_boundary_dependent": bool(disc_row is not None and not bool(disc_row["boundary_point"])),
        "c9_not_ridge_identified": ridge_ok(sens or {}, plan),
    }
    return {"tuple_id": row["tuple_id"], "stage": row["stage"], "backbone": row["backbone"],
            "comparator": comparator["tuple_id"], "primary_i_max": row["primary_i_max"],
            "dx2024_error_reduction_fraction": reduction, "latent_status": latent,
            **latent_values(checks, row["tuple_id"]),
            "advanced_status": advanced_status(checks, row["tuple_id"]), **clauses}


# ==========================================================================
# Report
# ==========================================================================

def _fmt(value, digits: int = 1) -> str:
    return fc._fmt(value, digits)


def _stage_journal(run: Run, stage: str) -> list[dict]:
    units = ["benchmark"] if stage == "benchmark" else \
        (discovery_units(run.plan) if stage == "discovery" else confirmation_units(run.plan))
    return [i for u in units for i in load_unit_journal(run.root, u) if not i.get("error")]


def _annual_long(journal: Sequence[dict], stage: str) -> pd.DataFrame:
    rows = []
    for item in journal:
        for series, values in item["annual_outputs"].items():
            for year, value in values.items():
                if int(year) >= 1996:
                    rows.append({"stage": stage, "tuple_id": item["tuple_id"], "backbone": item["backbone"],
                                 "model": item["model"], "seed": item["seed"], "series": series,
                                 "year": int(year), "value": float(value)})
        for key in ("mortality", "mortality_msm"):
            for r in item.get(key, []):
                if int(r["year"]) >= 1996:
                    rows.append({"stage": stage, "tuple_id": item["tuple_id"], "backbone": item["backbone"],
                                 "model": item["model"], "seed": item["seed"],
                                 "series": ("msm_" if key == "mortality_msm" else "all_") + r["series"],
                                 "year": int(r["year"]), "value": float(r["value"])})
    return pd.DataFrame(rows)


def write_report(run: Run, *, skip_figures: bool = False) -> dict:
    root, plan = run.root, run.plan
    targets = run.ctx.state["targets"]
    z = {t.key: t.z for t in targets}
    units = {u: _unit_state(root, u)["status"] for u in unit_names(plan)}
    frames, residual_frames, seed_frames = [], [], []
    bench_journal = _stage_journal(run, "benchmark")
    bench, bench_res, bench_seed = summarise(bench_journal, run.design["benchmark"], plan["benchmark"]["seeds"],
                                             targets)
    frames.append(bench), residual_frames.append(bench_res), seed_frames.append(bench_seed)
    discovery = pd.read_csv(root / "discovery_merged.csv") if (root / "discovery_merged.csv").is_file() \
        else pd.DataFrame()
    disc_res = pd.read_csv(root / "discovery_target_residuals.csv") \
        if (root / "discovery_target_residuals.csv").is_file() else pd.DataFrame()
    if not disc_res.empty:
        residual_frames.append(disc_res.assign(stage="discovery") if "stage" not in disc_res else disc_res)
    shortlist = _read(root / "frozen_shortlist.json") if (root / "frozen_shortlist.json").is_file() else None
    conf, conf_res, conf_seed = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    conf_journal = _stage_journal(run, "confirmation") if shortlist else []
    if shortlist:
        conf, conf_res, conf_seed = summarise(conf_journal, shortlist_tuples(run), plan["confirmation"]["seeds"],
                                              targets)
        frames.append(conf), residual_frames.append(conf_res), seed_frames.append(conf_seed)
    evaluated = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(
        not f.empty for f in frames) else pd.DataFrame()
    residuals = pd.concat([f for f in residual_frames if not f.empty], ignore_index=True) if any(
        not f.empty for f in residual_frames) else pd.DataFrame()
    seed_level = pd.concat([f for f in seed_frames if not f.empty], ignore_index=True) if any(
        not f.empty for f in seed_frames) else pd.DataFrame()

    checks = external_checks(evaluated, seed_level) if not evaluated.empty else pd.DataFrame()
    # Pairing: every M1 tuple against the M0 of its backbone on the same stage's seeds.
    pairs, paired_rows = [], []
    for stage_frame in (bench, conf):
        if stage_frame.empty:
            continue
        m0 = stage_frame[stage_frame.model == "M0"].set_index("backbone")
        for row in stage_frame[stage_frame.model == "M1"].to_dict(orient="records"):
            if row["backbone"] in m0.index:
                comp = m0.loc[row["backbone"], "tuple_id"]
                pairs.append((row["tuple_id"], comp))
                paired_rows.append({"stage": row["stage"], "tuple_id": row["tuple_id"], "comparator": comp,
                                    **paired(seed_level[seed_level.stage == row["stage"]], row["tuple_id"], comp)})
    paired_frame = pd.DataFrame(paired_rows)
    decomp = pd.concat([decomposition(seed_level[seed_level.stage == s], [(a, b) for a, b in pairs
                                                                          if a in set(f.tuple_id)])
                        for s, f in (("benchmark", bench), ("confirmation", conf)) if not f.empty],
                       ignore_index=True) if pairs else pd.DataFrame()

    finalists = [e["tuple_id"] for e in shortlist["entries"] if e["model"] == "M1"] if shortlist else []
    bench_anchor_ids = {i["origin"].split(":", 1)[1]: i["tuple_id"] for i in run.design["discovery"]
                        if i["origin"].startswith("anchor:")}
    ident = identifiability(discovery, disc_res, run.design, plan,
                            finalists + [bench_anchor_ids.get(t, t) for t in bench.tuple_id if t in bench_anchor_ids]) \
        if not discovery.empty and not disc_res.empty else {"available": False}
    disc_lookup = discovery.set_index("tuple_id") if not discovery.empty else None
    evaluations = []
    for row in evaluated[evaluated.model == "M1"].to_dict(orient="records") if not evaluated.empty else []:
        stage_frame = bench if row["stage"] == "benchmark" else conf
        comp = stage_frame[(stage_frame.model == "M0") & (stage_frame.backbone == row["backbone"])]
        if comp.empty:
            continue
        pair = next((p for p in paired_rows if p["tuple_id"] == row["tuple_id"] and p["stage"] == row["stage"]), {})
        disc_id = bench_anchor_ids.get(row["tuple_id"], row["tuple_id"])
        drow = disc_lookup.loc[disc_id] if disc_lookup is not None and disc_id in disc_lookup.index else None
        sens = ident.get("tuples", {}).get(disc_id) if ident.get("available") else None
        n = len(plan["benchmark"]["seeds"] if row["stage"] == "benchmark" else plan["confirmation"]["seeds"])
        evaluations.append(evaluate(row, comp.iloc[0].to_dict(), pair, checks, drow, sens, plan, residuals, n))
    evaluation_frame = pd.DataFrame(evaluations)
    design_complete = all(s == STATUS_COMPLETE for s in units.values()) and \
        run.manifest["gates"].get("discovery_merge", {}).get("status") == "COMPLETE"
    decision = classify(evaluations, design_complete=design_complete)

    out = {}

    def put(name: str, frame: pd.DataFrame) -> None:
        # Report products are regenerated from the journals on every report run.
        rcc.atomic_write_text(root / name, frame.to_csv(index=False))
        out[name] = sha_file(root / name)
    bench_seed_out = seed_level[seed_level.stage == "benchmark"] if not seed_level.empty else seed_level
    put("benchmark_seed_level.csv", bench_seed_out)
    put("paired_seed_differences.csv", paired_seed_differences(seed_level, pairs))
    put("benchmark_summary.csv", bench.merge(paired_frame[paired_frame.stage == "benchmark"]
                                             .drop(columns=["stage"]), on="tuple_id", how="left")
        if not bench.empty and not paired_frame.empty else bench)
    if not conf.empty:
        put("confirmation_summary.csv", conf.merge(paired_frame[paired_frame.stage == "confirmation"]
                                                   .drop(columns=["stage"]), on="tuple_id", how="left")
            if not paired_frame.empty else conf)
        put("confirmation_seed_level.csv", seed_level[seed_level.stage == "confirmation"])
    put("target_residuals.csv", residuals)
    put("external_plausibility_checks.csv", checks)
    put("stage_diagnosis_decomposition.csv", decomp)
    put("decision_clauses.csv", evaluation_frame)
    ledger_rows, latent_rows = [], []
    for stage, journal in (("benchmark", bench_journal), ("confirmation", conf_journal)):
        for item in journal:
            for r in item["ledger"]:
                ledger_rows.append({"stage": stage, "tuple_id": item["tuple_id"], "backbone": item["backbone"],
                                    "model": item["model"], "seed": item["seed"], **r})
            for r in item["snapshots"]:
                if int(r["snapshot_year"]) == 2025:
                    latent_rows.append({"stage": stage, "tuple_id": item["tuple_id"], "backbone": item["backbone"],
                                        "model": item["model"], "seed": item["seed"], **r})
    ledger = pd.DataFrame(ledger_rows)
    if not ledger.empty:
        if float(ledger.balance_residual.abs().max()) != 0.0:
            raise ContractError("stock-flow ledger does not close exactly")
        numeric = [c for c in ledger.columns if c.startswith("delta__") or
                   c in ("stock_start", "stock_end", "stock_change", "flow_sum", "balance_residual")]
        ledger = ledger.groupby(["stage", "tuple_id", "backbone", "model", "year", "activity", "metric"],
                                as_index=False)[numeric].mean()
    put("post2015_stock_flow.csv", ledger)
    latent = pd.DataFrame(latent_rows)
    if not latent.empty:
        latent = latent.groupby(["stage", "tuple_id", "backbone", "model", "series", "activity"],
                                as_index=False)["value"].agg(["mean", "std", "size"]).rename(columns={"size": "n_seeds"})
        latent.insert(0, "state_timing", "2024_year_end_equals_2025_starting_state")
    put("latent_state_2025.csv", latent)
    annual = pd.concat([_annual_long(bench_journal, "benchmark"), _annual_long(conf_journal, "confirmation")],
                       ignore_index=True)
    if not annual.empty:
        annual.to_csv(root / "seed_level_annual_1996_2025.csv.gz", index=False, compression="gzip")
        summary_annual = annual.groupby(["stage", "tuple_id", "backbone", "model", "series", "year"],
                                        as_index=False)["value"].agg(["mean", "std", "size"])
        put("annual_trajectories_1996_2025.csv", summary_annual)
    paths = []
    for item in run.design["benchmark"] + [t for t in run.design["discovery"] if t["tuple_id"] in finalists]:
        years = sorted(set(fc.reporting_years(item["config"], run.ctx.plan)) | set(range(1996, 2026)))
        values = fc.effective_msm_routine_probability(item["config"], years)
        for year, v in zip(years, values):
            paths.append({"tuple_id": item["tuple_id"], "model": item["model"], "year": year,
                          "effective_msm_routine_annual_probability": float(v),
                          "aids_symptom_annual_probability": float(item["vector"][SYMPTOM]) if item["model"] == "M1"
                          else 0.0})
    put("effective_msm_routine_paths.csv", pd.DataFrame(paths))
    write_state(root / "identifiability.json", ident)
    figures = [] if skip_figures else write_figures(run, bench, discovery, conf, checks, annual, z)
    lines = report_lines(run, units, bench, discovery, conf, shortlist, checks, paired_frame, decomp,
                         evaluation_frame, decision, ident, figures, z, design_complete)
    rcc.atomic_write_text(root / "REPORT.md", "\n".join(lines) + "\n")
    with manifest_lock(root):
        manifest = load_manifest(root)
        manifest["report"] = {"generated_utc": _now(), "decision": decision, "design_complete": design_complete,
                              "figures": figures, "output_sha256": out}
        if design_complete:
            manifest["status"] = "REPORTED_COMPLETE"
        save_manifest(root, manifest)
    return {"decision": decision, "design_complete": design_complete, "units": units}


def write_figures(run: Run, bench: pd.DataFrame, discovery: pd.DataFrame, conf: pd.DataFrame,
                  checks: pd.DataFrame, annual: pd.DataFrame, z: Mapping[str, float]) -> list[str]:
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    figdir = run.root / "figures"
    figdir.mkdir(exist_ok=True)
    written = []
    evaluated = pd.concat([f for f in (bench, conf) if not f.empty], ignore_index=True) \
        if not (bench.empty and conf.empty) else pd.DataFrame()
    if not evaluated.empty:
        fig, axes = plt.subplots(1, 2, figsize=(12, 5))
        for ax, (metric, check, title) in zip(axes, (("msm_undiagnosed_end2024", "E4", "MSM undiagnosed, end 2024"),
                                                     ("msm_new_infections_2024", "E1", "MSM new infections, 2024"))):
            spec = next(c for c in EXTERNAL_CHECKS if c["id"] == check)
            ax.axhspan(spec["lower"], spec["upper"], color="#d9d9d9", label="SHM 95% interval (Tier 2)")
            for i, row in enumerate(evaluated.itertuples()):
                ax.errorbar(i, getattr(row, metric), yerr=getattr(row, f"sd__{metric}"), fmt="o",
                            color="#1b7837" if row.model == "M1" else "black")
            ax.set_xticks(range(len(evaluated)), [f"{r.stage[:4]}:{r.tuple_id}" for r in evaluated.itertuples()],
                          rotation=75, fontsize=7)
            ax.set_title(title)
            ax.legend(fontsize=7)
        fig.suptitle("Latent-state checks (mean ± seed SD; black = M0, green = M1)")
        fig.tight_layout()
        fig.savefig(figdir / "fig1_latent_checks.png", dpi=130)
        plt.close(fig)
        written.append("figures/fig1_latent_checks.png")
    if not discovery.empty:
        fig, ax = plt.subplots(figsize=(8, 6))
        for cid, marker in zip(sorted(discovery.backbone.unique()), ("o", "s")):
            d = discovery[(discovery.backbone == cid) & ~discovery.is_identity]
            ax.scatter(100 * d.infection_change_vs_identity, 100 * d.dx2024_error_reduction_vs_identity,
                       c=d.primary_pass.map({True: "#1b7837", False: "#bbbbbb"}), marker=marker, s=16,
                       label=f"backbone {cid}")
        ax.axhline(25, color="grey", ls="--", lw=0.8)
        ax.axvline(0, color="grey", lw=0.8)
        ax.set_xlabel("MSM infections 2015-2024 vs identity (%)")
        ax.set_ylabel("2024 diagnosis |error| reduction vs identity (%)")
        ax.set_title("Discovery: diagnosis improvement vs infection change (green = primary pass)")
        ax.legend()
        fig.tight_layout()
        fig.savefig(figdir / "fig2_discovery_tradeoff.png", dpi=130)
        plt.close(fig)
        written.append("figures/fig2_discovery_tradeoff.png")
    if not annual.empty:
        dx = annual[annual.series == "new_diagnoses_msm"].groupby(["stage", "tuple_id", "model", "year"],
                                                                   as_index=False)["value"].mean()
        fig, ax = plt.subplots(figsize=(9, 5))
        for (stage, tid, model), g in dx.groupby(["stage", "tuple_id", "model"]):
            ax.plot(g.year, g.value, lw=1.6 if model == "M0" else 0.9, color="black" if model == "M0" else None,
                    label=f"{stage[:4]}:{tid}")
        obs_years = [t for t in (1996, 2000, 2004, 2010, 2015, 2019, 2024)]
        ax.scatter(obs_years, [z[f"shm_new_diagnoses_msm@{y}"] for y in obs_years], color="red", zorder=5,
                   label="SHM primary targets")
        ax.set_xlabel("year")
        ax.set_ylabel("MSM diagnoses")
        ax.legend(fontsize=6, ncol=2)
        ax.set_title("Annual MSM diagnoses 1996-2025 (seed means)")
        fig.tight_layout()
        fig.savefig(figdir / "fig3_msm_diagnoses_trajectories.png", dpi=130)
        plt.close(fig)
        written.append("figures/fig3_msm_diagnoses_trajectories.png")
    if not evaluated.empty:
        fig, ax = plt.subplots(figsize=(9, 5))
        x = np.arange(len(evaluated))
        ax.bar(x, evaluated.msm_dx_in_aids_2024, color=["black" if m == "M0" else "#1b7837"
                                                        for m in evaluated.model])
        ax.axhline(58, color="red", ls="--", label="observed advanced (CD4<200 or AIDS), MSM+trans 2024 = 58")
        ax.set_xticks(x, [f"{r.stage[:4]}:{r.tuple_id}" for r in evaluated.itertuples()], rotation=75, fontsize=7)
        ax.set_ylabel("model MSM diagnoses in clinical AIDS, 2024")
        ax.set_title("Representable component vs the one-sided advanced-presentation bound")
        ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(figdir / "fig4_advanced_presentation_bound.png", dpi=130)
        plt.close(fig)
        written.append("figures/fig4_advanced_presentation_bound.png")
    if len(written) > 5:
        raise ContractError("no more than five figures")
    return written


def report_lines(run, units, bench, discovery, conf, shortlist, checks, paired_frame, decomp, evaluation,
                 decision, ident, figures, z, design_complete) -> list[str]:
    plan = run.plan
    evaluated = pd.concat([f for f in (bench, conf) if not f.empty], ignore_index=True) \
        if not (bench.empty and conf.empty) else pd.DataFrame()
    m1 = evaluated[evaluated.model == "M1"] if not evaluated.empty else evaluated
    L = ["# Diagnosis-mechanism triangulation", "",
         f"Design `{DESIGN}`. Structural model comparison for a preliminary result; not production adoption "
         "and not an HTA baseline. Production configuration, source workbook, previous outputs and HTA baseline "
         "are unchanged.", "",
         "Unit status: " + ", ".join(f"{u} **{s}**" for u, s in units.items()) + ".", ""]
    if not design_complete:
        L += ["> **PARTIAL_TIME_LIMIT / incomplete design.** Nothing below treats a partial archive as the "
              "completed design.", ""]
    L += ["## First-page answers", ""]
    L.append("1. **Simulator AIDS definition.** The AIDS compartment is entered through Chronic-to-AIDS "
             "progression sourced to CASCADE 2000/2003, which count only the clinical part of the 1993 CDC "
             "definition (CD4 <200 without an AIDS-defining illness is not AIDS). Mortality (Glaubius CD4 <200 "
             "bands), the off-ART utility and the Popping 'very late' cost tier are CD4-based. The compartment "
             "is therefore clinical AIDS with CD4-based downstream parameters; see "
             "`AIDS_DEFINITION_ADJUDICATION.md`.")
    L.append("2. **Comparable advanced-presentation evidence.** Direct: none. Approximate: the AIDS proxy "
             "(RIVM, primary). One-sided upper bounds only: SHM 2024 advanced presentation in MSM+trans (58; 21% "
             "of classifiable MSM) and Op de Coul 1996-2014 MSM advanced 28.7%. Unavailable: the CD4 <200 without "
             "AIDS component (SHM 2022-2024 pooled: 183 of 391 advanced) and CD4 <350 late presentation.")
    if not m1.empty and not evaluation.empty:
        rows = []
        for e in evaluation.itertuples():
            rows.append(f"`{e.stage[:5]}:{e.tuple_id}` |error| reduction {_fmt(100 * e.dx2024_error_reduction_fraction)}% "
                        f"({'material' if e.c2_material_2024_improvement else 'not material'}; primary "
                        f"{'pass' if e.c1_primary_pass_fresh_seeds else 'fail'})")
        L.append("3. **Does M1 improve diagnoses on fresh seeds?** " + "; ".join(rows) +
                 f". Observed 2024: {_fmt(z['shm_new_diagnoses_msm@2024'], 0)}.")
        parts = []
        if not decomp.empty:
            for tid, g in decomp.groupby("tuple_id", sort=False):
                v = g.set_index("component").paired_mean_change
                parts.append(
                    f"`{tid}`: +{_fmt(v.get('total_change_msm_diagnoses_2015_2024'))} diagnoses 2015-2024 = "
                    f"reservoir carried into 2015 {_fmt(v.get('a_entering_undiagnosed_stock_2015'))} + extra "
                    f"infections {_fmt(v.get('b_additional_infections_2015_2024'))} + change in reservoir left at "
                    f"2025 {_fmt(v.get('c_reservoir_left_at_2025'))} + other exits "
                    f"{_fmt(v.get('d_other_exits_2015_2024'))}; symptom-attributed "
                    f"{_fmt(v.get('symptom_attributed_aids_stage_dx'))}")
        L.append("4. **Improvement vs infections and reservoir (exact accounting identity, paired vs M0).** " +
                 ("; ".join(parts) if parts else "not available") +
                 ". A negative (c) means a larger reservoir is left undiagnosed at 2025. Diagnosis timing with "
                 "infections held fixed is not identifiable from saved draws (no counterfactual run).")
        lat = []
        for e in evaluation.to_dict("records"):
            lat.append(f"`{e['tuple_id']}` {e['latent_status']} ({_latent_text(e)})")
        m0 = evaluated[evaluated.model == "M0"]
        m0_lat = [f"`{t}` {latent_status(checks, t)}" for t in m0.tuple_id]
        L.append("5. **Latent compatibility (Tier-2, SHM ECDC-tool estimates; fitted to the same diagnoses).** "
                 "M1: " + "; ".join(lat) + ". M0: " + "; ".join(m0_lat) + ".")
        rows = []
        for r in evaluated.itertuples():
            rows.append(f"`{r.tuple_id}` ART {_fmt(r.msm_on_art_end2024, 0)}, PLHIV {_fmt(r.msm_plhiv_end2024, 0)}, "
                        f"MSM HIV deaths 2015-2024 {_fmt(r.msm_hiv_deaths_2015_2024, 0)}")
        L.append("6. **ART, deaths, PLHIV, 2025 starting state (MSM, seed means).** " + "; ".join(rows) + ".")
    else:
        L += ["3-6. **Not available**: no completed fresh-seed M1 evaluation."]
    if not evaluation.empty and "c7_non_isolated_region" in evaluation:
        robust = evaluation[evaluation[[k for k, _ in CLAUSES[:4]]].all(axis=1)]
        pool = robust if not robust.empty else evaluation
        if (pool.c7_non_isolated_region & pool.c8_not_boundary_dependent).any():
            verdict = "broad (a non-isolated interior region exists)"
        elif pool.c7_non_isolated_region.any():
            verdict = "boundary-dependent (non-isolated only at a bound or search-box face)"
        elif (~pool.c8_not_boundary_dependent).all():
            verdict = "isolated and boundary-dependent"
        else:
            verdict = "narrow or isolated"
        extra = ""
        if not discovery.empty and "same_qualitative_behaviour" in discovery:
            n = discovery[~discovery.is_identity]
            outer = n[n.ring == "outer"]
            extra = (f" Discovery: {int(n.same_qualitative_behaviour.sum())}/{len(n)} vectors pass and improve 2024 "
                     f"vs identity; outer ring {int(outer.same_qualitative_behaviour.sum())}/{len(outer)}.")
        L.append(f"7. **Region.** **{verdict}** ({'robust' if not robust.empty else 'all'} evaluated tuples: "
                 f"non-isolated {int(pool.c7_non_isolated_region.sum())}/{len(pool)}, boundary-dependent "
                 f"{int((~pool.c8_not_boundary_dependent).sum())}/{len(pool)})." + extra)
    else:
        L.append("7. **Region.** Not available until discovery is merged.")
    if ident.get("available"):
        parts = []
        for cid, b in ident["backbones"].items():
            raw = b["raw"]
            ok = ridge_ok(raw, plan)
            parts.append(f"backbone {cid}: **{'separately informed' if ok else 'not separately informed'}** "
                         f"(rank {raw.get('rank_at_1pct', 'n/a')}/4, condition {_fmt(raw.get('condition_number'))}, "
                         f"symptom column explained by routine columns R^2="
                         f"{_fmt(raw.get('symptom_column_explained_by_routine_r2'), 2)}; thresholds: condition <= "
                         f"{plan['decision_thresholds']['identifiability_max_condition_number']}, R^2 <= "
                         f"{plan['decision_thresholds']['identifiability_max_symptom_r2']})")
        L.append("8. **Routine vs symptom identification (exploratory local sensitivity, not posterior).** " +
                 "; ".join(parts) + ".")
    else:
        L.append("8. **Identification.** Not available.")
    L.append(f"9. **Classification.** **{decision['label']}** (rule {decision.get('rule')}). {decision['reason']}.")
    L.append(f"10. **Smallest next step.** {decision.get('next_step')}")
    L += ["", "### Clause table", ""]
    if not evaluation.empty:
        keys = [k for k, _ in CLAUSES]
        L += ["| tuple | latent | " + " | ".join(k.split("_", 1)[0] for k in keys) + " |",
              "|---|---|" + "---|" * len(keys)]
        for e in evaluation.to_dict(orient="records"):
            L.append(f"| `{e['stage'][:5]}:{e['tuple_id']}` | {e['latent_status']} | " +
                     " | ".join("yes" if e[k] else "no" for k in keys) + " |")
        L += [""] + [f"- **{k.split('_', 1)[0]}**: {text}." for k, text in CLAUSES]
    L += ["", "## Benchmark (30 fresh seeds per tuple)", ""]
    if not bench.empty:
        cols = ["tuple_id", "model", "primary_i_max", "primary_pass", "dx2024", "dx2024_abs_error", "aids2019",
                "aids2022", "msm_new_infections_2024", "msm_undiagnosed_end2024", "msm_dx_in_aids_2024",
                "xval_aids1990", "xval_aids1995"]
        L += [bench[[c for c in cols if c in bench]].to_markdown(index=False, floatfmt=".2f"), ""]
        L.append(f"Historical cross-validation (never scored): observed MSM AIDS 1990 = 318, 1995 = 314.")
    L += ["", "## Discovery", ""]
    if not discovery.empty:
        n = discovery[~discovery.is_identity]
        L.append(f"{len(n)} M1 vectors merged ({run.manifest['gates'].get('discovery_merge', {}).get('status')}); "
                 f"primary passes {int(n.primary_pass.sum())}; same qualitative behaviour "
                 f"{int(n.same_qualitative_behaviour.sum())}; outer-ring passes "
                 f"{int(n[n.ring == 'outer'].primary_pass.sum())}/{int((n.ring == 'outer').sum())}.")
    if shortlist:
        L += ["", "## Frozen shortlist", "",
              pd.DataFrame(shortlist["entries"])[["tuple_id", "model", "role", "selected_as",
                                                  "discovery_primary_pass", "discovery_dx2024_abs_error"]]
              .to_markdown(index=False, floatfmt=".2f")]
    L += ["", "## Confirmation (30 fresh seeds per tuple)", ""]
    if not conf.empty:
        cols = ["tuple_id", "model", "primary_i_max", "primary_pass", "dx2024", "dx2024_abs_error", "aids2019",
                "aids2022", "msm_new_infections_2024", "msm_undiagnosed_end2024", "msm_dx_in_aids_2024"]
        L += [conf[[c for c in cols if c in conf]].to_markdown(index=False, floatfmt=".2f")]
    else:
        L.append("No completed confirmation.")
    L += ["", "## External checks (predeclared)", ""]
    if not checks.empty:
        view = checks[checks.check_id.isin(["E1", "E4", "E6", "E7", "E12", "E13", "E13f", "E14"])]
        L += [view[["tuple_id", "check_id", "tier", "type", "observed", "lower", "upper", "model_mean", "status"]]
              .to_markdown(index=False, floatfmt=".3g")]
    L += ["", "## Integrity", "",
          "- Stage counters equal endogenous diagnoses; the AIDS proxy equals its two components; no AIDS-stage "
          "diagnosis before 1985 (checked per trajectory, a failure stops it).",
          "- The post-2015 undiagnosed-MSM ledger closes exactly; the decomposition identity is checked.",
          "- Tier-2 series never enter a likelihood.", "", "## Figures", ""] + [f"- `{f}`" for f in figures] + [
          "", f"**{decision['label']}**"]
    return L


# ==========================================================================
# Status, resume, run-all
# ==========================================================================

def write_status_md(root: Path, manifest: dict) -> None:
    lines = [f"# Status: {DESIGN}", "", f"Updated {manifest.get('updated_utc', _now())}.", "",
             f"Overall: **{manifest.get('status')}**", "",
             f"Clock used (union of dispatch intervals): {manifest.get('clock_used_seconds', 0.0):.0f} s "
             f"of {manifest.get('time', {}).get('stop_dispatch_seconds', 6600)} s dispatch stop.", "",
             "| unit | status | planned | completed | failed |", "|---|---|---|---|---|"]
    for unit, u in manifest.get("units", {}).items():
        lines.append(f"| {unit} | {u.get('status')} | {u.get('planned')} | {u.get('completed')} | {u.get('failed')} |")
    lines += ["", f"Shortlist sha256: `{manifest.get('shortlist_sha256')}`",
              "", "Gates: " + json.dumps(manifest.get("gates", {}), default=str)[:2000]]
    rcc.atomic_write_text(Path(root) / "STATUS.md", "\n".join(lines) + "\n")


def status(root: Path, plan_path: Path) -> dict:
    manifest = load_manifest(root)
    out = {"status": manifest.get("status"), "plan_frozen": manifest.get("plan_frozen"),
           "clock_used_seconds": clock_used_seconds(root) if manifest.get("plan_frozen") else 0.0}
    if manifest.get("plan_frozen"):
        run = load_run(root, plan_path, check_modules=False)
        units = {}
        for unit in unit_names(run.plan):
            if unit.startswith("confirmation") and not manifest.get("shortlist_sha256"):
                units[unit] = {"status": _unit_state(root, unit)["status"], "note": "awaiting shortlist"}
                continue
            rep = integrity_report(run, [unit])
            units[unit] = {"status": _unit_state(root, unit)["status"], "expected": rep["expected"],
                           "completed": rep["completed"], "missing": len(rep["missing"]),
                           "failed": len(rep["failed"]), "duplicates": len(rep["duplicates"]),
                           "mismatched": len(rep["foreign_or_mismatched"])}
        out["units"] = units
        out["shortlist_sha256"] = manifest.get("shortlist_sha256")
        out["gates"] = manifest.get("gates")
        with manifest_lock(root):
            current = load_manifest(root)
            current["clock_used_seconds"] = clock_used_seconds(root)
            write_status_md(root, current)
    return out


def next_unit(run: Run) -> str | None:
    for unit in ["benchmark"] + discovery_units(run.plan):
        if _unit_state(run.root, unit)["status"] not in (STATUS_COMPLETE, STATUS_SKIPPED_RESERVE):
            return unit
    if run.manifest.get("shortlist_sha256"):
        for unit in confirmation_units(run.plan):
            if _unit_state(run.root, unit)["status"] != STATUS_COMPLETE:
                return unit
    return None


def resume(run: Run, *, workers: int, shortlist_sha256: str | None = None, **kw) -> dict:
    unit = next_unit(run)
    if unit is None:
        return {"note": "no incomplete dispatch unit before the next gate (merge / freeze / report)"}
    if unit.startswith("confirmation") and shortlist_sha256 is None:
        return {"note": f"{unit} is next but needs --shortlist-sha256 (review gate)"}
    has_journal = bool(load_unit_journal(run.root, unit))
    return run_unit(run, unit, workers=workers, resume=has_journal, shortlist_sha256=shortlist_sha256, **kw)


def run_all(root: Path, plan_path: Path, *, workers: int, approve_shortlist_sha256: str | None,
            skip_figures: bool, **kw) -> dict:
    log = []
    run = load_run(root, plan_path)
    for unit in ["benchmark"] + discovery_units(run.plan):
        run = load_run(root, plan_path, ctx=run.ctx)
        state = _unit_state(root, unit)["status"]
        if state in (STATUS_COMPLETE, STATUS_SKIPPED_RESERVE):
            log.append({unit: state})
            continue
        result = run_unit(run, unit, workers=workers, resume=bool(load_unit_journal(root, unit)), **kw)
        log.append(result)
        if result["status"] not in (STATUS_COMPLETE, STATUS_SKIPPED_RESERVE):
            return {"stopped_at": unit, "log": log}
    run = load_run(root, plan_path, ctx=run.ctx)
    if "discovery_merge" not in run.manifest["gates"]:
        partial = any(_unit_state(root, u)["status"] != STATUS_COMPLETE for u in discovery_units(run.plan))
        log.append(merge_discovery(run, allow_partial=partial))
        run = load_run(root, plan_path, ctx=run.ctx)
    if run.manifest.get("shortlist_sha256") is None:
        log.append(freeze_shortlist(run))
        run = load_run(root, plan_path, ctx=run.ctx)
    if approve_shortlist_sha256 is None:
        return {"stopped_at": "shortlist review gate", "log": log,
                "next": "review frozen_shortlist.json, then rerun run-all with --approve-shortlist-sha256 "
                        f"{run.manifest['shortlist_sha256']}"}
    for unit in confirmation_units(run.plan):
        run = load_run(root, plan_path, ctx=run.ctx)
        if _unit_state(root, unit)["status"] == STATUS_COMPLETE:
            continue
        result = run_unit(run, unit, workers=workers, resume=bool(load_unit_journal(root, unit)),
                          shortlist_sha256=approve_shortlist_sha256, **kw)
        log.append(result)
        if result["status"] != STATUS_COMPLETE:
            break
    run = load_run(root, plan_path, ctx=run.ctx)
    log.append(merge_confirmation(run))
    report = write_report(load_run(root, plan_path, ctx=run.ctx), skip_figures=skip_figures)
    return {"stopped_at": "report", "log": log, "report": report}


# ==========================================================================
# CLI
# ==========================================================================

def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["audit-evidence", "validate-plan", "smoke", "benchmark", "discover",
                                            "merge-discovery", "freeze-shortlist", "confirm",
                                            "merge-confirmation", "report", "status", "resume", "run-all"])
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--smoke-dir", type=Path, default=DEFAULT_SMOKE_OUTPUT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--shard-count", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--shortlist-sha256")
    parser.add_argument("--approve-shortlist-sha256")
    parser.add_argument("--allow-partial-discovery", action="store_true")
    parser.add_argument("--skip-figures", action="store_true")
    args = parser.parse_args(argv)
    root, plan_path = args.output_dir, args.plan
    if args.command == "audit-evidence":
        result = audit_evidence(plan_path, root)
    elif args.command == "validate-plan":
        result = validate_plan(plan_path, root)
    elif args.command == "status":
        result = status(root, plan_path)
    elif args.command == "smoke":
        result = smoke(root, plan_path, args.smoke_dir)
    else:
        run = load_run(root, plan_path)
        print(f"output root: {Path(root).resolve()}\nfrozen plan sha256: {run.manifest['frozen_plan_sha256']}",
              file=sys.stderr)
        if args.command in ("discover", "confirm"):
            stage = "discovery" if args.command == "discover" else "confirmation"
            expected = int(run.plan[stage]["shard_count"])
            if args.shard_index is None or args.shard_count != expected or \
                    not 0 <= args.shard_index < expected:
                raise ContractError(f"{args.command} needs --shard-index K --shard-count {expected}")
            result = run_unit(run, f"{stage}_{args.shard_index:02d}", workers=args.workers, resume=args.resume,
                              shortlist_sha256=args.shortlist_sha256)
        elif args.command == "benchmark":
            result = run_unit(run, "benchmark", workers=args.workers, resume=args.resume)
        elif args.command == "merge-discovery":
            result = merge_discovery(run, allow_partial=args.allow_partial_discovery)
        elif args.command == "freeze-shortlist":
            result = freeze_shortlist(run)
        elif args.command == "merge-confirmation":
            result = merge_confirmation(run)
        elif args.command == "report":
            result = write_report(run, skip_figures=args.skip_figures)
        elif args.command == "resume":
            result = resume(run, workers=args.workers, shortlist_sha256=args.shortlist_sha256)
        else:
            result = run_all(root, plan_path, workers=args.workers,
                             approve_shortlist_sha256=args.approve_shortlist_sha256, skip_figures=args.skip_figures)
    print(json.dumps(ccs.json_safe(result), indent=2, default=str))


if __name__ == "__main__":
    main()
