#!/usr/bin/env python3
"""Runner for the robust-NROY freeze stage.

    .venv/bin/python run_robust_nroy_freeze.py validate --plan configs/robust_nroy_freeze_production.json
    .venv/bin/python run_robust_nroy_freeze.py prepare  --plan <plan> --out <dir> --anchors <csv>
    .venv/bin/python run_robust_nroy_freeze.py evaluate --stage-dir <dir> --shard 0 --of 4
    .venv/bin/python run_robust_nroy_freeze.py merge    --stage-dir <dir>
    .venv/bin/python run_robust_nroy_freeze.py report   --stage-dir <dir>
    .venv/bin/python run_robust_nroy_freeze.py resume   --stage-dir <dir>

Nothing here starts a production simulation on its own.

* ``validate`` never simulates.  It is the dry-run gate: it reports every
  unsigned decision and exits non-zero while any remain.
* Every other subcommand loads the plan through
  :meth:`nroy_contract.FrozenPlan.load`, which **refuses** while a required
  value is unsigned.  With the production plan as shipped, every subcommand
  below ``validate`` therefore stops before touching the engine.
* ``evaluate`` requires an explicit ``--evaluator``.  ``surrogate`` is for the
  verification suite and runs no engine; ``production`` is the real simulator
  and is refused unless the plan is signed *and* ``--i-have-priced-the-budget``
  is passed, because the production spec requires a priced trajectory budget
  and a measured pilot before launch.

A worker may evaluate only the frozen ledger rows assigned to its shard.  It
cannot generate, substitute or re-draw a proposal: the design exists only in
the prepared stage directory and is loaded read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Callable, Mapping, Sequence

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
import nroy_contract as nc
import nroy_design as nd
import nroy_execution as ne
import nroy_graph as ng
import nroy_report as nrp
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parent

QUESTION = (
    "Are fresh-seed-confirmed parameter vectors supported by stable neighbouring "
    "parameter combinations, including thin correlated ridges, or is no such support "
    "detected under the declared finite design?"
)


# ==========================================================================
# validate
# ==========================================================================

def command_validate(args: argparse.Namespace) -> int:
    """Dry-run validation.  Runs no simulation under any circumstances."""
    path = Path(args.plan)
    if not path.is_absolute():
        path = ROOT / path
    raw = json.loads(path.read_text())
    pending = nc.unsigned_values(raw)

    print(f"run plan: {path}")
    print(f"plan sha256: {ccs.sha256_file(path)}")
    print()
    if not pending:
        plan = nc.FrozenPlan.load(path)
        print("All required decisions are signed.")
        print(f"  targets:           {plan.targets.n} ({plan.targets.hash()[:16]})")
        print(f"  discovery seeds:   K={plan.k_discovery}")
        print(f"  confirmation seeds: K={plan.k_confirmation}")
        print(f"  kernel quotas:     {plan.kernel_quotas()}")
        print()
        print("Signed is not the same as launch-ready: the production spec also "
              "requires a pilot, a priced trajectory budget and the full verification "
              "suite to pass.")
        return 0

    print(f"FAIL CLOSED: {len(pending)} required value(s) are unsigned.")
    print("No production simulation may start.\n")
    without = [item for item in pending if item.proposal is None]
    with_proposal = [item for item in pending if item.proposal is not None]
    if with_proposal:
        print(f"-- {len(with_proposal)} with a proposal recorded in the specifications --")
        for item in with_proposal:
            print(f"  {item.path}")
            print(f"      proposal: {item.proposal!r}")
    if without:
        print(f"\n-- {len(without)} with NO proposal in either specification "
              "(these need a decision, not a transcription) --")
        for item in without:
            print(f"  {item.path}: {item.description}")
    return 2


# ==========================================================================
# prepare
# ==========================================================================

def _load_anchors(path: Path, model: nc.ModelSpace) -> list[nd.Anchor]:
    """Load confirmed anchors as complete joint vectors.

    A missing coordinate is fatal.  Vectors are never reconstructed from
    marginals, defaults or filenames.
    """
    frame = ne.read_frame_exact(path)
    if "candidate_id" not in frame.columns:
        raise ContractError(f"{path}: anchor table needs a 'candidate_id' column")
    missing = [name for name in model.names if name not in frame.columns]
    if missing:
        raise ContractError(
            f"{path}: anchor table is missing parameter column(s) {missing}. "
            "Anchors must be complete joint vectors."
        )
    ids = frame["candidate_id"].astype(str)
    if ids.duplicated().any():
        raise ContractError(f"{path}: duplicate candidate_id {sorted(ids[ids.duplicated()])}")
    return [
        nd.Anchor.build(
            str(row["candidate_id"]),
            {name: float(row[name]) for name in model.names},
            model,
        )
        for _, row in frame.iterrows()
    ]


def _model_spaces(plan: nc.FrozenPlan) -> dict[str, nc.ModelSpace]:
    m0_path = Path(str(plan.require("models.m0.calib_space")))
    if not m0_path.is_absolute():
        m0_path = ROOT / m0_path
    m0_raw = json.loads(m0_path.read_text())
    spaces = {nc.MODEL_M0: nc.load_model_space(m0_raw, model=nc.MODEL_M0)}
    p_name = str(plan.require("p_parameter.name"))
    p_min = float(plan.require("p_parameter.min"))
    p_max = float(plan.require("p_parameter.max"))
    m1_declared = plan.get("models.m1.calib_space")
    if nc.is_unsigned(m1_declared):
        m1_raw = nc.build_m1_calib_space(
            m0_raw,
            p_name=p_name,
            p_config_path=str(plan.require("p_parameter.config_path")),
            p_min=p_min,
            p_max=p_max,
        )
    else:
        m1_path = Path(str(m1_declared))
        if not m1_path.is_absolute():
            m1_path = ROOT / m1_path
        m1_raw = json.loads(m1_path.read_text())
    spaces[nc.MODEL_M1] = nc.load_model_space(
        m1_raw, model=nc.MODEL_M1, p_name=p_name, p_min=p_min, p_max=p_max
    )
    return spaces


def build_designs(
    plan: nc.FrozenPlan,
    model: nc.ModelSpace,
    anchors: Sequence[nd.Anchor],
    *,
    stage: str = nc.STAGE_DISCOVERY,
    archive: Sequence[Mapping[str, float]] = (),
    archive_ids: Sequence[str] = (),
    injection_check: Callable[[dict[str, float]], None] | None = None,
) -> tuple[list[nd.Proposal], list[nd.KernelOutcome]]:
    """Generate every frozen probe for every anchor, then enforce the quotas.

    Quotas are checked here, *before* dispatch.  A kernel that cannot fill its
    quota raises :class:`nroy_design.KernelExhaustedError` and preparation
    fails; it never borrows another kernel's budget.
    """
    identity_epsilon = (
        float(plan.require("p_parameter.identity_epsilon"))
        if model.p_name is not None else None
    )
    validator = nd.Validator(
        space=model.space,
        model=model,
        identity_epsilon=identity_epsilon,
        injection_check=injection_check,
    )
    seed = int(plan.seeds.proposal_rng)
    scales = plan.ambient_scales()
    quota_per_scale = int(plan.require("ambient.quota_per_scale"))
    generator = str(plan.require("ambient.direction_generator"))
    semantics = str(plan.require("ambient.scale_semantics"))
    min_anchor = float(plan.require("separation.min_anchor_neighbour"))
    min_neighbour = float(plan.require("separation.min_neighbour_neighbour"))
    max_attempts = int(plan.require("ambient.max_attempts_per_anchor"))

    proposals: list[nd.Proposal] = []
    outcomes: list[nd.KernelOutcome] = []

    for anchor in anchors:
        offset = 0
        for scale in scales:
            outcome = nd.ambient_probes(
                anchor, model, validator, stage=stage, scale=scale,
                quota=quota_per_scale, generator=generator, scale_semantics=semantics,
                seed=seed, min_anchor_sep=min_anchor, min_neighbour_sep=min_neighbour,
                max_attempts=max_attempts, index_offset=offset,
            )
            outcomes.append(outcome)
            proposals.extend(outcome.accepted)
            offset += 1000

        directions = list(nd.archive_directions(
            anchor, model, archive=archive, archive_ids=archive_ids,
            neighbour_count=int(plan.require("graph.neighbour_count")),
            rank_truncation=int(plan.require("archive_following.rank_truncation")),
        )) if archive else []
        directions += nd.confirmed_difference_directions(
            anchor, model,
            confirmed=[a.params for a in anchors],
            confirmed_ids=[a.anchor_id for a in anchors],
            rank_truncation=int(plan.require("archive_following.rank_truncation")),
        )
        archive_outcome = nd.directed_probes(
            anchor, model, validator, stage=stage, kernel=nc.KERNEL_ARCHIVE_FOLLOWING,
            directions=directions,
            scales=[float(s) for s in plan.require("archive_following.scales")],
            quota=int(plan.require("archive_following.quota")), seed=seed,
            min_anchor_sep=min_anchor, min_neighbour_sep=min_neighbour,
            max_attempts=max_attempts, index_offset=offset,
        )
        outcomes.append(archive_outcome)
        proposals.extend(archive_outcome.accepted)
        offset += 1000

        cross = nd.cross_block_directions(
            anchor, model, seed=seed,
            count=max(1, int(plan.require("cross_block.quota"))),
        )
        cross_outcome = nd.directed_probes(
            anchor, model, validator, stage=stage, kernel=nc.KERNEL_CROSS_BLOCK,
            directions=cross,
            scales=[float(s) for s in plan.require("cross_block.scales")],
            quota=int(plan.require("cross_block.quota")), seed=seed,
            min_anchor_sep=min_anchor, min_neighbour_sep=min_neighbour,
            max_attempts=max_attempts, index_offset=offset,
        )
        outcomes.append(cross_outcome)
        proposals.extend(cross_outcome.accepted)
        offset += 1000

        if model.p_name is not None:
            boundary = nd.p_boundary_probes(
                anchor, model, validator, stage=stage,
                p_bins=[float(b) for b in plan.require("p_parameter.bins")],
                identity_epsilon=float(identity_epsilon), seed=seed, index_offset=offset,
            )
            outcomes.append(boundary)
            proposals.extend(boundary.accepted)

    quotas = {
        kernel: quota * len(anchors) for kernel, quota in plan.kernel_quotas().items()
    }
    nd.assert_quotas(outcomes, quotas, what=f"{stage}/{model.model} preparation")
    return proposals, outcomes


def command_prepare(args: argparse.Namespace) -> int:
    plan = nc.FrozenPlan.load(args.plan)
    spaces = _model_spaces(plan)
    model = spaces[args.model]
    anchors = _load_anchors(Path(args.anchors), model)
    if not anchors:
        raise ContractError("no anchors supplied; there is nothing to assess")

    if model.model == nc.MODEL_M1:
        nc.assert_m1_identity_preconditions(
            routine_hazard_scale=float(plan.require(
                "p_parameter.routine_hazard_scale_identity_value")),
            required_value=1.0,
        )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    proposals, outcomes = build_designs(plan, model, anchors, stage=args.stage)

    seeds = plan.seeds.seeds_for(args.stage)
    code_hash = _code_sha256()
    units = tuple(
        ne.WorkUnit(
            stage=args.stage, model=model.model, design_id=p.design_id, seed=int(seed),
            vector_hash=p.vector_hash, kernel=p.kernel, parent_ids=p.parent_ids,
            params=p.params, parent_manifest_sha256=str(args.parent_manifest_sha256 or ""),
            code_sha256=code_hash, config_sha256=plan.plan_sha256,
            target_schema=plan.targets.frozen_order,
        )
        for p in proposals for seed in seeds
    )
    ledger = ne.Ledger(stage=args.stage, model=model.model, units=units)
    for shards in (1, 2, 3, 4, 7):
        ledger.assert_shard_partition(shards)

    ne.atomic_write_frame(
        out / "design_proposals.csv",
        pd.DataFrame([p.as_row() for p in proposals]),
    )
    ne.atomic_write_frame(
        out / "design_vectors.csv",
        pd.DataFrame([{"design_id": p.design_id, **p.params} for p in proposals]),
    )
    ne.atomic_write_frame(
        out / "kernel_outcomes.csv",
        pd.DataFrame([{**o.as_row(), "rejection_counts": json.dumps(o.rejection_counts)}
                      for o in outcomes]),
    )
    ne.atomic_write_frame(
        out / "anchors.csv",
        pd.DataFrame([{"candidate_id": a.anchor_id, "vector_hash": a.vector_hash,
                       **a.params} for a in anchors]),
    )
    ledger.save(out)
    ne.write_stage_manifest(
        out, stage=args.stage, model=model.model, status=nc.STATUS_PREPARED,
        ledger=ledger, contract=plan.manifest_contract(), parent_manifest=None,
        extra={"n_anchors": len(anchors), "n_proposals": len(proposals)},
    )
    budget = int(plan.require("budget.max_trajectories"))
    print(f"PREPARED {args.stage}/{model.model} in {out}")
    print(f"  anchors:    {len(anchors)}")
    print(f"  proposals:  {len(proposals)}")
    print(f"  work units: {len(units)} (= proposals x K={len(seeds)})")
    print(f"  declared trajectory budget: {budget}")
    if len(units) > budget:
        print("  WARNING: the frozen design exceeds the declared trajectory budget.")
    print("\nNo simulation has been run. Dispatch with 'evaluate'.")
    return 0


# ==========================================================================
# evaluate (worker)
# ==========================================================================

#: Modules whose content defines the scientific behaviour of a stage.  Hashed
#: at prepare, re-checked by every worker, and recorded in the manifest.
CODE_MODULES = ("nroy_contract", "nroy_design", "nroy_execution", "nroy_graph")


def _code_sha256() -> str:
    return ccs.sha256_json({
        name: ccs.sha256_file(ROOT / f"{name}.py") for name in CODE_MODULES
    })


def _stage_directory(path: str) -> Path:
    """Resolve a prepared stage directory, refusing anything unusable.

    A missing directory or manifest is a clear contract error, not a traceback:
    the caller pointed at something that was never prepared.
    """
    stage_dir = Path(path)
    if not stage_dir.exists():
        raise ContractError(f"stage directory does not exist: {stage_dir}")
    manifest = stage_dir / "manifest.json"
    if not manifest.exists():
        raise ContractError(
            f"{stage_dir} has no manifest.json, so it is not a prepared stage. "
            "Run 'prepare' first."
        )
    return stage_dir


def surrogate_evaluator(
    unit: ne.WorkUnit, params: Mapping[str, float], targets: Sequence[str]
) -> tuple[float, ...]:
    """A deterministic stand-in for the simulator.  Runs no engine.

    Used by the verification suite so that sharding, merge, resume and
    reporting can be tested end to end without a single production
    trajectory.  It depends only on the vector hash and the seed, so it is
    reproducible and shard-order independent.
    """
    rng = np.random.default_rng(
        [int(unit.seed), int(unit.vector_hash[:16], 16) % (2 ** 63)]
    )
    return tuple(100.0 + 10.0 * rng.random(len(targets)))


def production_evaluator(
    unit: ne.WorkUnit, params: Mapping[str, float], targets: Sequence[str]
) -> tuple[float, ...]:
    """The real simulator path.  Deliberately not implemented here.

    Wiring this to :class:`run_v3.SimulationEngineV3` is the step that turns a
    frozen design into spent compute.  The production specification requires a
    signed plan, a measured pilot and a priced trajectory budget first, none of
    which exist yet, so this raises rather than quietly running.
    """
    raise ContractError(
        "the production evaluator is not wired up. Running it requires a signed plan, "
        "a measured pilot and a priced trajectory budget (PRODUCTION_LOCKIN_SPEC "
        "sections 3-4). Use --evaluator surrogate for verification."
    )


EVALUATORS: dict[str, Callable] = {
    "surrogate": surrogate_evaluator,
    "production": production_evaluator,
}


def command_evaluate(args: argparse.Namespace) -> int:
    stage_dir = _stage_directory(args.stage_dir)
    manifest = json.loads((stage_dir / "manifest.json").read_text())
    plan = nc.FrozenPlan.load(args.plan or manifest["contract"]["plan_path"])
    ledger = ne.Ledger.load(stage_dir)
    targets = plan.targets

    if args.evaluator == "production" and not args.i_have_priced_the_budget:
        raise ContractError(
            "refusing to launch the production evaluator without "
            "--i-have-priced-the-budget. Launch attempts consume budget and are never "
            "refunded."
        )
    evaluate = EVALUATORS[args.evaluator]

    # A worker evaluates FROZEN work. If the plan or the code it just loaded is
    # not the one the ledger was frozen against, the results would carry the
    # stale frozen hash and the merge would see one uniform, wrong version.
    # Refuse here rather than launder the hash.
    frozen_config = {u.config_sha256 for u in ledger.units}
    if len(frozen_config) == 1 and plan.plan_sha256 not in frozen_config:
        raise ContractError(
            f"this worker loaded run plan {plan.plan_sha256}, but the ledger was frozen "
            f"against {next(iter(frozen_config))}. The plan changed after prepare; "
            "re-prepare rather than evaluating frozen work against an edited plan."
        )
    frozen_code = {u.code_sha256 for u in ledger.units}
    current_code = _code_sha256()
    if len(frozen_code) == 1 and current_code not in frozen_code:
        raise ContractError(
            f"this worker is running code {current_code}, but the ledger was frozen "
            f"against {next(iter(frozen_code))}. Re-prepare rather than mixing versions."
        )

    vectors = ne.read_frame_exact(stage_dir / "design_vectors.csv").set_index("design_id")
    assigned = ledger.rows_for_shard(args.shard, args.of)
    done = ledger.completed_keys()
    meta = ne.worker_metadata()

    results: list[ne.SeedResult] = []
    for unit in assigned:
        if unit.key in done:
            continue  # a completed work unit is never rerun
        params = {
            name: float(vectors.loc[unit.design_id, name])
            for name in vectors.columns
        }
        if nc.vector_hash(params, names=tuple(vectors.columns)) != unit.vector_hash:
            raise ContractError(
                f"{unit.design_id}: the design vector does not match the ledger hash; "
                "refusing to evaluate a mutated design"
            )
        attempt_id = ledger.next_attempt_id(unit.design_id, unit.seed)
        started = time.time()
        try:
            values = evaluate(unit, params, targets.frozen_order)
            result = ne.SeedResult(
                design_id=unit.design_id, seed=unit.seed, stage=unit.stage, model=unit.model,
                vector_hash=unit.vector_hash, target_order=targets.frozen_order,
                values=tuple(float(v) for v in values), attempt_id=attempt_id,
                code_sha256=current_code, config_sha256=plan.plan_sha256,
                parent_manifest_sha256=unit.parent_manifest_sha256,
            )
            ledger.record_attempt(ne.AttemptRecord(
                attempt_id=attempt_id, design_id=unit.design_id, seed=unit.seed,
                status=ne.ATTEMPT_SUCCESS, started_utc=started, ended_utc=time.time(),
                vector_hash=unit.vector_hash, result_hash=result.result_hash, **meta,
            ))
            results.append(result)
        except ContractError:
            raise
        except Exception as exc:  # noqa: BLE001 - the failure is data, not a crash
            ledger.record_attempt(ne.AttemptRecord(
                attempt_id=attempt_id, design_id=unit.design_id, seed=unit.seed,
                status=ne.ATTEMPT_FAILED, started_utc=started, ended_utc=time.time(),
                vector_hash=unit.vector_hash,
                failure_reason=str(exc), failure_class=type(exc).__name__, **meta,
            ))

    shard_dir = stage_dir / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([r.as_row() for r in results])
    ne.atomic_write_frame(
        shard_dir / f"shard_{args.shard:03d}_of_{args.of:03d}.csv", frame,
        overwrite_completed=True,
    )
    # Only this shard's own attempt file. Rewriting the shared ledger here
    # would clobber the other workers' audit rows (and did).
    ledger.save_attempts(stage_dir, f"shard_{args.shard:03d}_of_{args.of:03d}")
    print(f"shard {args.shard}/{args.of}: {len(results)} result(s) from "
          f"{len(assigned)} assigned unit(s) [{args.evaluator}]")
    return 0


# ==========================================================================
# merge
# ==========================================================================

def _read_shards(stage_dir: Path, targets: nc.TargetContract) -> list[ne.SeedResult]:
    out: list[ne.SeedResult] = []
    shard_dir = stage_dir / "shards"
    if not shard_dir.exists():
        return out
    columns = [f"target:{name}" for name in targets.frozen_order]
    for path in sorted(shard_dir.glob("shard_*.csv")):
        frame = ne.read_frame_exact(path)
        if frame.empty:
            continue
        missing = [c for c in columns if c not in frame.columns]
        if missing:
            raise ContractError(f"{path}: missing target columns {missing}")
        for _, row in frame.iterrows():
            out.append(ne.SeedResult(
                design_id=str(row["design_id"]), seed=int(row["seed"]),
                stage=str(row["stage"]), model=str(row["model"]),
                vector_hash=str(row["vector_hash"]),
                target_order=targets.frozen_order,
                values=tuple(float(row[c]) for c in columns),
                attempt_id=str(row["attempt_id"]),
                code_sha256=str(row["code_sha256"]), config_sha256=str(row["config_sha256"]),
            ))
    return out


def command_merge(args: argparse.Namespace) -> int:
    stage_dir = _stage_directory(args.stage_dir)
    manifest = json.loads((stage_dir / "manifest.json").read_text())
    plan = nc.FrozenPlan.load(args.plan or manifest["contract"]["plan_path"])
    ledger = ne.Ledger.load(stage_dir)

    results = _read_shards(stage_dir, plan.targets)
    resume = ne.plan_resume(
        ledger,
        max_attempts=int(plan.require("budget.max_attempts")),
        max_trajectories=int(plan.require("budget.max_trajectories")),
    )
    complete = resume.status == nc.STATUS_COMPLETE
    seed_frame = ne.merge_seed_results(
        ledger, results, targets=plan.targets, expect_complete=complete
    )
    ne.atomic_write_frame(stage_dir / "seed_results.csv", seed_frame, overwrite_completed=True)

    observed = json.loads(Path(args.observed).read_text()) if args.observed else None
    base_variance = json.loads(Path(args.base_variance).read_text()) if args.base_variance else None
    if observed and base_variance:
        scores = ne.recompute_candidate_scores(
            seed_frame, targets=plan.targets, observed=observed,
            base_variance=base_variance, cutoff=plan.cutoff,
            variance_alpha=float(plan.require("promotion.variance_alpha")),
        )
        ne.atomic_write_frame(
            stage_dir / "candidate_scores.csv", scores, overwrite_completed=True
        )
        print(f"recomputed scores for {len(scores)} candidate(s) from seed-level rows")

    ne.write_stage_manifest(
        stage_dir, stage=manifest["stage"], model=manifest["model"],
        status=resume.status, ledger=ledger, contract=plan.manifest_contract(),
        parent_manifest=None, extra={"resume": resume.as_row()},
        overwrite_completed=True,
    )
    print(f"merged {len(seed_frame)} seed-level row(s); stage status {resume.status}")
    if resume.status != nc.STATUS_COMPLETE:
        print(f"  {resume.reason}")
        print("  a PARTIAL stage cannot become a parent for the next stage")
    return 0


# ==========================================================================
# report
# ==========================================================================

def command_report(args: argparse.Namespace) -> int:
    stage_dir = _stage_directory(args.stage_dir)
    manifest = json.loads((stage_dir / "manifest.json").read_text())
    plan_path = args.plan or manifest["contract"]["plan_path"]
    raw = json.loads(Path(plan_path).read_text())
    unsigned = [item.path for item in nc.unsigned_values(raw)]

    tables: dict[str, pd.DataFrame] = {}
    for name in (
        "design_proposals", "seed_results", "candidate_scores", "local_support",
        "candidate_edges", "edge_verdicts", "components", "representatives",
        "kernel_audit", "ledger_units", "ledger_attempts", "p_coverage",
    ):
        path = stage_dir / f"{name}.csv"
        if path.exists():
            frame = ne.read_frame_exact(path)
            if not frame.empty or name in ("ledger_units", "ledger_attempts"):
                tables[name] = frame

    facts = nrp.summary_facts(
        tables, status=str(manifest["status"]), model=str(manifest["model"]),
        manifest=manifest,
    )
    written = nrp.write_package(
        stage_dir / "package", tables, facts=facts, manifest=manifest,
        question=QUESTION, unsigned=unsigned,
        kernel_audit=tables.get("kernel_audit"),
        resume=(manifest.get("extra") or {}).get("resume"),
        recomputation_verified="candidate_scores" in tables,
        overwrite_completed=True,
    )
    print(f"wrote {len(written)} artifact(s) to {stage_dir / 'package'}")
    for name in sorted(written):
        print(f"  {name}")
    return 0


def command_resume(args: argparse.Namespace) -> int:
    stage_dir = _stage_directory(args.stage_dir)
    manifest = json.loads((stage_dir / "manifest.json").read_text())
    plan = nc.FrozenPlan.load(args.plan or manifest["contract"]["plan_path"])
    ledger = ne.Ledger.load(stage_dir)
    decision = ne.plan_resume(
        ledger,
        max_attempts=int(plan.require("budget.max_attempts")),
        max_trajectories=int(plan.require("budget.max_trajectories")),
    )
    for key, value in decision.as_row().items():
        print(f"  {key}: {value}")
    return 0 if decision.status == nc.STATUS_COMPLETE else 3


# ==========================================================================
# CLI
# ==========================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    default_plan = "configs/robust_nroy_freeze_production.json"

    p = sub.add_parser("validate", help="dry-run validation; never simulates")
    p.add_argument("--plan", default=default_plan)
    p.set_defaults(func=command_validate)

    p = sub.add_parser("prepare", help="freeze the design and the ledger")
    p.add_argument("--plan", default=default_plan)
    p.add_argument("--out", required=True)
    p.add_argument("--anchors", required=True, help="CSV of complete joint confirmed vectors")
    p.add_argument("--model", choices=list(nc.MODELS), default=nc.MODEL_M0)
    p.add_argument("--stage", choices=list(nc.STAGE_ORDER), default=nc.STAGE_DISCOVERY)
    p.add_argument("--parent-manifest-sha256", default="")
    p.set_defaults(func=command_prepare)

    p = sub.add_parser("evaluate", help="worker: evaluate frozen rows of one shard")
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--of", type=int, required=True)
    p.add_argument("--evaluator", choices=sorted(EVALUATORS), required=True)
    p.add_argument("--i-have-priced-the-budget", action="store_true")
    p.set_defaults(func=command_evaluate)

    p = sub.add_parser("merge", help="validate and consolidate shard outputs")
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.add_argument("--observed", default=None, help="JSON mapping target -> observed value")
    p.add_argument("--base-variance", default=None, help="JSON mapping target -> frozen B_i")
    p.set_defaults(func=command_merge)

    p = sub.add_parser("report", help="write the human-readable artifact package")
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.set_defaults(func=command_report)

    p = sub.add_parser("resume", help="report what a resume may relaunch")
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.set_defaults(func=command_resume)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except nc.UnsignedConfigError as exc:
        print(f"FAIL CLOSED: {exc}", file=sys.stderr)
        return 2
    except ContractError as exc:
        print(f"CONTRACT ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
