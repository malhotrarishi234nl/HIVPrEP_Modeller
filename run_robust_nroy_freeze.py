#!/usr/bin/env python3
"""Runner for the robust-NROY freeze stage.

    .venv/bin/python run_robust_nroy_freeze.py validate  --plan configs/robust_nroy_freeze_production.json
    .venv/bin/python run_robust_nroy_freeze.py prepare   --plan <plan> --out <dir> --anchors <csv>
    .venv/bin/python run_robust_nroy_freeze.py evaluate  --stage-dir <dir> --shard 0 --of 4
    .venv/bin/python run_robust_nroy_freeze.py merge     --stage-dir <dir> --observed <json> --base-variance <json>
    .venv/bin/python run_robust_nroy_freeze.py classify  --stage-dir <dir>
    .venv/bin/python run_robust_nroy_freeze.py regions   --parent-dir <bridges dir> --out <dir>
    .venv/bin/python run_robust_nroy_freeze.py diagnose  --stage-dir <dir>
    .venv/bin/python run_robust_nroy_freeze.py report    --stage-dir <dir>
    .venv/bin/python run_robust_nroy_freeze.py resume    --stage-dir <dir>

The whole chain, for one model::

    prepare --stage discovery    -> evaluate -> merge -> classify
    prepare --stage promotion    -> evaluate -> merge -> classify   (parent: discovery)
    prepare --stage confirmation -> evaluate -> merge -> classify   (parent: promotion)
    prepare --stage bridges      -> evaluate -> merge               (parent: confirmation)
    regions                                                        (parent: bridges)

One command here runs the engine, and only one::

    .venv/bin/python run_robust_nroy_freeze.py identity-gate --out <dir>

That is the M0/M1-at-``p=0`` identity gate.  It is a **pre-production
command, not a test**: every pytest that checks the identity is skipped under
the repository's no-engine guard, so a green full suite does not establish it.
``prepare --model M1`` requires its passing artifact and refuses a missing,
failed, incomplete or stale one.  The gate's runs are short truncated
trajectories, about a second each; nothing is written to ``outputs/``.

Nothing else here starts a simulation on its own.

* ``validate`` never simulates.  It is the dry-run gate: it reports every
  unsigned decision and exits non-zero while any remain.
* Every other subcommand loads the plan through
  :meth:`nroy_contract.FrozenPlan.load`, which **refuses** while a required
  value is unsigned.  With the production plan as shipped, every subcommand
  below ``validate`` therefore stops before touching the engine.
* ``evaluate`` requires an explicit ``--evaluator``.  ``surrogate`` and
  ``surrogate_islands`` are for the verification suite and run no engine --
  the second is a closed-form field that is plausible only on a few isolated
  islands, which is how the suite builds a supported bridge between two points
  with no local support **without** varying the acceptance inputs between
  stages; ``production`` is the real simulator
  and is refused unless the plan is signed *and* ``--i-have-priced-the-budget``
  is passed, because the production spec requires a priced trajectory budget
  and a measured pilot before launch.
* Every stage beyond the first freezes its parent's complete
  **acceptance-input identity** at ``prepare`` -- target order, target-register
  hash, observed-value hash, base-variance hash, discrepancy, scoring version
  and acceptance rule -- and ``merge``, ``classify`` and ``regions`` each fail
  closed on any difference.  One lineage, one acceptance basis.

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
import nroy_assess as nas
import nroy_contract as nc
import nroy_design as nd
import nroy_execution as ne
import nroy_graph as ng
import nroy_identity_gate as nig
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
# Acceptance-input identity: one acceptance basis per lineage
# ==========================================================================

#: Name of the file a child stage writes at ``prepare`` to freeze its parent's
#: complete acceptance-input identity.  It is written before the manifest, so
#: it is covered by the stage's own artifact index and cannot be edited later
#: without that index noticing.
PARENT_IDENTITY_FILENAME = "parent_acceptance_identity.json"


def _recorded_acceptance_identity(manifest: Mapping) -> dict | None:
    """The acceptance-input identity a stage recorded at its own merge."""
    extra = manifest.get("extra") or {}
    recorded = extra.get("acceptance_input_identity")
    return dict(recorded) if recorded else None


def _freeze_parent_acceptance_identity(
    out: Path, parent_manifest: Mapping, *, parent_dir: Path
) -> dict:
    """Freeze the parent's complete acceptance-input identity into the child.

    A child stage's verdicts are only comparable with its parent's if both were
    scored against the same observed values, the same frozen base variances,
    the same target register and order, the same discrepancy, the same scoring
    arithmetic and the same acceptance rule.  Recording the parent *manifest*
    hash does not establish that: the manifest hash changes for a hundred
    irrelevant reasons and did not cover the two acceptance-input files at all.

    So the identity is frozen here, once, at the moment the parent is
    validated, and re-checked at the child's own merge and before any
    classification or edge verdict.
    """
    identity = _recorded_acceptance_identity(parent_manifest)
    if not identity:
        raise ContractError(
            f"the parent stage at {parent_dir} records no acceptance-input identity, so "
            "there is nothing for this stage to freeze and no way to show the two share "
            "one acceptance basis. Re-run 'merge' on the parent with --observed and "
            "--base-variance under the current code."
        )
    ne.atomic_write_json(out / PARENT_IDENTITY_FILENAME, {
        "frozen_from": str(parent_dir),
        "parent_stage": str(parent_manifest.get("stage") or ""),
        "parent_manifest_sha256": ccs.sha256_json(nc.json_safe(parent_manifest)),
        "acceptance_input_identity": identity,
        "meaning": (
            "the parent's complete acceptance-input identity, frozen at prepare. This "
            "stage's merge, classification and edge verdicts must all match it exactly."
        ),
    })
    return identity


def _frozen_parent_acceptance_identity(stage_dir: Path) -> dict | None:
    """The parent identity this stage froze at prepare, if it has a parent."""
    path = Path(stage_dir) / PARENT_IDENTITY_FILENAME
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    identity = payload.get("acceptance_input_identity")
    if not identity:
        raise ContractError(
            f"{path} exists but records no acceptance-input identity; refusing to treat "
            "an empty freeze as agreement"
        )
    return dict(identity)


def _assert_acceptance_basis(
    stage_dir: Path, manifest: Mapping, plan: nc.FrozenPlan, *, what: str
) -> dict:
    """Re-check a merged stage's acceptance basis before it is interpreted.

    Two comparisons, both fail-closed:

    * against the **current plan**, for everything the plan fixes (target
      order, register hash, discrepancy, scoring version, acceptance rule), so
      a plan edited between ``merge`` and ``classify`` cannot slip through;
    * against the **frozen parent identity**, for the complete identity
      including the observed-value and base-variance hashes, so a child cannot
      be classified on a different acceptance basis than its parent.

    A stage with no recorded identity is refused: it was merged before the
    acceptance inputs were hashed, and nothing can establish what it was
    scored against.
    """
    recorded = _recorded_acceptance_identity(manifest)
    if not recorded:
        raise ContractError(
            f"{stage_dir} records no acceptance-input identity, so what its scores mean "
            "cannot be established. Re-run 'merge' with --observed and --base-variance "
            f"before {what}."
        )
    nc.assert_acceptance_identity(
        plan.plan_acceptance_fields(), recorded,
        what=f"{what}: this stage against the run plan it is being read with",
        fields=nc.PLAN_DERIVED_ACCEPTANCE_FIELDS,
    )
    frozen_parent = _frozen_parent_acceptance_identity(stage_dir)
    if frozen_parent is not None:
        nc.assert_acceptance_identity(
            frozen_parent, recorded,
            what=f"{what}: this stage against its frozen parent identity",
        )
    return recorded


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


def _load_archive(
    path: str | None, model: nc.ModelSpace
) -> tuple[list[dict[str, float]], list[str], list[float] | None]:
    """Load the declared archive pool of low-J vectors, or nothing.

    Archive vectors are complete joint vectors, exactly like anchors: a missing
    coordinate is fatal rather than filled in.  The ``j`` column is optional
    here and required by :func:`nroy_design.archive_directions` only when the
    signed neighbour rule orders by J, so a plan that signs the low-J rule
    without supplying J fails closed at design time.
    """
    if not path:
        return [], [], None
    frame = ne.read_frame_exact(Path(path))
    if "candidate_id" not in frame.columns:
        raise ContractError(f"{path}: archive table needs a 'candidate_id' column")
    missing = [name for name in model.names if name not in frame.columns]
    if missing:
        raise ContractError(
            f"{path}: archive table is missing parameter column(s) {missing}. Archive "
            "vectors must be complete joint vectors."
        )
    vectors = [
        {name: float(row[name]) for name in model.names} for _, row in frame.iterrows()
    ]
    ids = [str(value) for value in frame["candidate_id"]]
    j_values = (
        [float(value) for value in frame["j"]] if "j" in frame.columns else None
    )
    return vectors, ids, j_values


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
    archive_j: Sequence[float] | None = None,
    injection_check: Callable[[dict[str, float]], None] | None = None,
) -> tuple[list[nd.Proposal], list[nd.KernelOutcome]]:
    """Generate every frozen probe for every anchor, then enforce the quotas.

    Quotas are checked here, *before* dispatch.  A kernel that cannot fill its
    quota raises :class:`nroy_design.KernelExhaustedError` and preparation
    fails; it never borrows another kernel's budget.
    """
    validator = nd.Validator(
        space=model.space,
        model=model,
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

    # The declared correlation-following sources. `assert_cross_block_declared`
    # refuses a plan whose cross-block quota and declared pool disagree, so the
    # source pool cannot be a decorative list alongside a quota that ignores it.
    source_pool = [str(k) for k in plan.require("archive_following.source_pool")]
    run_cross_block = nd.assert_cross_block_declared(
        source_pool, int(plan.require("cross_block.quota"))
    )
    archive_rule = str(plan.require("archive_following.neighbour_rule"))
    archive_neighbours = int(plan.require("archive_following.neighbour_count"))

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

        directions = nd.correlation_following_directions(
            anchor, model,
            source_pool=source_pool,
            archive=archive, archive_ids=archive_ids, archive_j=archive_j,
            confirmed=[a.params for a in anchors],
            confirmed_ids=[a.anchor_id for a in anchors],
            neighbour_count=archive_neighbours,
            rank_truncation=int(plan.require("archive_following.rank_truncation")),
            neighbour_rule=archive_rule,
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

        if run_cross_block:
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
                extra_values=[
                    float(v) for v in (plan.get("p_parameter.extra_probe_values") or ())
                ],
                seed=seed, index_offset=offset,
            )
            outcomes.append(boundary)
            proposals.extend(boundary.accepted)

    quotas = {
        kernel: quota * len(anchors) for kernel, quota in plan.kernel_quotas().items()
    }
    nd.assert_quotas(outcomes, quotas, what=f"{stage}/{model.model} preparation")
    return proposals, outcomes


def build_bridge_designs(
    plan: nc.FrozenPlan,
    model: nc.ModelSpace,
    anchors: Sequence[nd.Anchor],
    nodes: pd.DataFrame,
    *,
    stage: str = nc.STAGE_BRIDGES,
    injection_check: Callable[[dict[str, float]], None] | None = None,
) -> tuple[list[nd.Proposal], list[nd.KernelOutcome], list[ng.CandidateEdge],
           list[ng.CandidateEdge]]:
    """Freeze the candidate-edge graph and its bridge probes (spec sec. 4).

    Every graph decision is read from the signed plan: the candidate-edge rule,
    the neighbour count, the tie-breaking rule, the minimal-edge algorithm and
    the interpolation fractions.  None of them has a default here.

    Only the **minimal** edge set receives probes.  That is the smallest set of
    bridges whose evaluation can establish the component structure, so the
    simulation budget goes to edges that can actually change the answer; the
    full proposal set is still written for audit.
    """
    geometry = nas.node_geometry(nodes)
    edges = ng.candidate_edges(
        geometry,
        neighbour_rule=str(plan.require("graph.neighbour_rule")),
        neighbour_count=int(plan.require("graph.neighbour_count")),
        tie_breaking=str(plan.require("graph.tie_breaking")),
    )
    minimal = ng.minimal_edge_set(
        edges,
        algorithm=str(plan.require("graph.minimal_edge_algorithm")),
        node_ids=sorted(geometry),
    )
    fractions = plan.bridge_fractions()
    validator = nd.Validator(space=model.space, model=model, injection_check=injection_check)
    by_id = {anchor.anchor_id: anchor for anchor in anchors}
    seed = int(plan.seeds.proposal_rng)

    proposals: list[nd.Proposal] = []
    outcomes: list[nd.KernelOutcome] = []
    for index, edge in enumerate(sorted(minimal, key=lambda e: e.edge_id)):
        for endpoint in (edge.left, edge.right):
            if endpoint not in by_id:
                raise ContractError(
                    f"edge {edge.edge_id} names {endpoint!r}, which is not a loaded anchor; "
                    "no stage may reconstruct a parent from a filename or a default"
                )
        outcome = nd.bridge_probes(
            by_id[edge.left], by_id[edge.right], model, validator,
            stage=stage, fractions=fractions, seed=seed, edge_id=edge.edge_id,
            index_offset=index * 1000,
        )
        outcomes.append(outcome)
        proposals.extend(outcome.accepted)

    if minimal:
        nd.assert_quotas(
            outcomes,
            {nc.KERNEL_BRIDGE: len(fractions) * len(minimal)},
            what=f"{stage}/{model.model} bridge preparation",
        )
    return proposals, outcomes, edges, minimal


def _parent_tables(parent_dir: Path, model: nc.ModelSpace) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Anchors and their completed local assessment, from the parent stage."""
    anchors_path = parent_dir / "anchors.csv"
    support_path = parent_dir / "local_support.csv"
    if not anchors_path.exists():
        raise ContractError(f"parent stage has no anchors.csv: {anchors_path}")
    if not support_path.exists():
        raise ContractError(
            f"parent stage has no local_support.csv: {support_path}. Run 'classify' on the "
            "parent stage first: a confirmed point may not enter the graph or representative "
            "selection without a completed local assessment."
        )
    return ne.read_frame_exact(anchors_path), ne.read_frame_exact(support_path)


def command_prepare(args: argparse.Namespace) -> int:
    plan = nc.FrozenPlan.load(args.plan)
    spaces = _model_spaces(plan)
    model = spaces[args.model]
    is_bridges = args.stage == nc.STAGE_BRIDGES

    if model.model == nc.MODEL_M1:
        nc.assert_m1_identity_preconditions(
            routine_hazard_scale=float(plan.require(
                "p_parameter.routine_hazard_scale_identity_value")),
            required_value=1.0,
        )
        # The identity is a production prerequisite, and the pytests that check
        # it are SKIPPED under the no-engine guard, so a green full suite does
        # not establish it. An M1 design therefore requires the gate artifact,
        # and `assert_gate_satisfied` refuses a missing, failed, incomplete or
        # stale one.
        if not args.identity_gate:
            raise ContractError(
                "preparing an M1 design requires --identity-gate pointing at a passing "
                f"{nig.GATE_FILENAME}. Produce it with\n"
                "  .venv/bin/python run_robust_nroy_freeze.py identity-gate --out <dir>\n"
                "ROBUST_NROY_FREEZE_SPEC section 2 requires the multi-vector, multi-seed "
                "identity before M1 is launched, and the pytests that check it are "
                "skipped under the no-engine guard."
            )
        gate = nig.assert_gate_satisfied(args.identity_gate)
        print(f"M0/M1 identity gate: PASSED ({gate['n_passed']}/{gate['n_checks']} checks, "
              f"{gate['generated_utc']})")

    # A stage beyond the first must descend from a validated, COMPLETE parent.
    # The guard existed and was tested but was not connected: prepare took the
    # parent hash as free text, so `--stage confirmation` could be run against
    # nothing at all, and a PARTIAL stage could parent the next one.
    # 'prepare' is a command, not a produced stage: the discovery stage is the
    # first thing written and has no predecessor directory. Every stage after
    # it must descend from a validated, COMPLETE parent directory.
    required_parent = nc.STAGE_PARENT.get(args.stage)
    if required_parent == nc.STAGE_PREPARE:
        required_parent = None
    parent_manifest = None
    parent_manifest_sha256 = ""
    if required_parent is not None:
        if not args.parent_dir:
            raise ContractError(
                f"stage {args.stage!r} descends from {required_parent!r}, so "
                "--parent-dir is required. No stage beyond 'prepare' may be produced "
                "without a validated parent."
            )
        parent_manifest = ne.validate_parent_stage(
            _stage_directory(args.parent_dir),
            expected_stage=required_parent,
            expected_model=model.model,
            targets=plan.targets,
            seeds=plan.seeds,
            child_stage=args.stage,
        )
        parent_manifest_sha256 = ccs.sha256_json(nc.json_safe(parent_manifest))
        if args.parent_manifest_sha256 and args.parent_manifest_sha256 != parent_manifest_sha256:
            raise ContractError(
                f"declared parent manifest hash {args.parent_manifest_sha256} does not "
                f"match the validated parent ({parent_manifest_sha256})"
            )
    elif args.parent_dir:
        raise ContractError(
            f"stage {args.stage!r} has no parent, but --parent-dir was supplied"
        )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    extra: dict = {}
    if is_bridges:
        # The bridges stage takes its node set from the parent's completed
        # local assessment, not from a hand-supplied anchor file: the graph
        # must be built over exactly the points the parent confirmed and
        # assessed, and nothing else.
        if args.anchors:
            raise ContractError(
                "--anchors is not accepted for the bridges stage. Its node set comes from "
                "the validated parent's anchors.csv and local_support.csv, so the graph "
                "cannot be built over a different set of points than the one assessed."
            )
        parent_anchors, parent_support = _parent_tables(
            _stage_directory(args.parent_dir), model
        )
        anchors = [
            nd.Anchor.build(
                str(row["candidate_id"]),
                {name: float(row[name]) for name in model.names},
                model,
            )
            for _, row in parent_anchors.iterrows()
        ]
        nodes = nas.graph_nodes_table(
            anchors=parent_anchors, local_support=parent_support, model=model
        )
        proposals, outcomes, edges, minimal = build_bridge_designs(
            plan, model, anchors, nodes, stage=args.stage
        )
        tables = ng.graph_tables(edges, minimal, [], [])
        ne.atomic_write_frame(out / "graph_nodes.csv", nodes)
        ne.atomic_write_frame(out / "candidate_edges.csv", tables["candidate_edges"])
        # Carried forward verbatim so the bridges package reports the same
        # local assessment the graph was built from.
        ne.atomic_write_frame(out / "local_support.csv", parent_support)
        eligible = sum(1 for value in nas.node_eligibility(nodes).values() if value)
        extra = {
            "n_nodes": len(nodes),
            "n_eligible_nodes": eligible,
            "n_candidate_edges": len(edges),
            "n_minimal_edges": len(minimal),
            "bridge_fractions": list(plan.bridge_fractions()),
        }
        print(f"  graph: {len(nodes)} node(s), {eligible} eligible, "
              f"{len(edges)} proposed edge(s), {len(minimal)} in the minimal set")
    else:
        if not args.anchors:
            raise ContractError(
                f"stage {args.stage!r} requires --anchors: a CSV of complete joint "
                "confirmed vectors"
            )
        anchors = _load_anchors(Path(args.anchors), model)
        archive, archive_ids, archive_j = _load_archive(args.archive, model)
        proposals, outcomes = build_designs(
            plan, model, anchors, stage=args.stage,
            archive=archive, archive_ids=archive_ids, archive_j=archive_j,
        )
    if not anchors:
        raise ContractError("no anchors supplied; there is nothing to assess")

    seeds = plan.seeds.seeds_for(args.stage)
    code_hash = _code_sha256()
    units = tuple(
        ne.WorkUnit(
            stage=args.stage, model=model.model, design_id=p.design_id, seed=int(seed),
            vector_hash=p.vector_hash, kernel=p.kernel, parent_ids=p.parent_ids,
            params=p.params, parent_manifest_sha256=parent_manifest_sha256,
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
    frozen_identity = None
    if parent_manifest is not None:
        frozen_identity = _freeze_parent_acceptance_identity(
            out, parent_manifest, parent_dir=_stage_directory(args.parent_dir)
        )
    ne.write_stage_manifest(
        out, stage=args.stage, model=model.model, status=nc.STATUS_PREPARED,
        ledger=ledger, contract=plan.manifest_contract(),
        parent_manifest=parent_manifest,
        extra={
            "n_anchors": len(anchors), "n_proposals": len(proposals),
            **({"parent_acceptance_identity": frozen_identity} if frozen_identity else {}),
            **extra,
        },
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
CODE_MODULES = (
    "nroy_contract", "nroy_design", "nroy_execution", "nroy_graph", "nroy_assess",
)


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


#: Plausible islands for the ``surrogate_islands`` evaluator, as positions of
#: the **first declared coordinate** in normalised space; every other
#: coordinate sits at 0.5.  Equally spaced on purpose, so that two anchors
#: placed one spacing beyond each end put the 25/50/75% bridge probes exactly
#: on the three islands.
ISLAND_CENTRES = (0.35, 0.5, 0.65)

#: Island radius in normalised Euclidean distance.  Small enough that no
#: ambient probe (declared scales 0.025 and 0.05) around an anchor one spacing
#: away can reach one.
ISLAND_RADIUS = 0.02


def island_field_i_max(u: Sequence[float]) -> float:
    """``I_max`` of a field that is plausible only on a few isolated islands.

    Closed form, deterministic in the vector, and arranged so that a point is
    plausible (``I_max <= 3``) only within :data:`ISLAND_RADIUS` of an island
    centre.  This is the honest way to build the "no local support but a
    supported bridge" case: *one* acceptance basis, applied identically at
    every stage, over a geometry where the plausible set lies between the
    anchors rather than at them.  It is also a fair caricature of the thing
    this stage exists to detect, and of the thing it must not overclaim:
    bridge probes confirming a straight path says nothing about continuity.
    """
    point = np.asarray(u, dtype=float)
    best = min(
        float(np.linalg.norm(point - island_centre(position, point.size)))
        for position in ISLAND_CENTRES
    )
    return 1.0 if best <= ISLAND_RADIUS else 1.0 + 1000.0 * best


def island_centre(position: float, n: int) -> np.ndarray:
    """One island centre: ``position`` on the first coordinate, 0.5 elsewhere."""
    centre = np.full(int(n), 0.5)
    centre[0] = float(position)
    return centre


def _island_surrogate(space: nc.ModelSpace) -> Callable:
    """The island field as an evaluator.  Runs no engine; verification only.

    With ``observed = 0`` and ``B_i = 1`` the recomputed ``I_max`` of the first
    target reproduces the field, which is the convention the rest of the
    verification suite already uses.  A tiny per-seed perturbation keeps the
    replicate variance non-degenerate without moving any verdict.
    """
    def evaluate(
        unit: ne.WorkUnit, params: Mapping[str, float], targets: Sequence[str]
    ) -> tuple[float, ...]:
        u = space.normalise([float(params[name]) for name in space.names])
        signal = island_field_i_max(u) + 1e-6 * float(int(unit.seed) % 3)
        return tuple([signal] + [0.0] * (len(targets) - 1))

    return evaluate


class ProductionEvaluator:
    """The real simulator, behind every fail-closed protection in this stage.

    Construction is the gate, and it is the only way to obtain a callable that
    touches :class:`run_v3.SimulationEngineV3`.  To build one, *all* of the
    following must already hold:

    * the plan loaded, which means every required decision is signed and the
      ``sign_off`` block is complete (:meth:`nroy_contract.FrozenPlan.load`);
    * the frozen production model config declared and present on disk;
    * the frozen target register resolving **every** target in the contract,
      in the contract's order -- a target that cannot be resolved is fatal
      here rather than dropped, because a silently shorter target vector is a
      weaker test wearing the same name;
    * the caller having passed ``--evaluator production`` explicitly (there is
      no default) and ``--i-have-priced-the-budget``;
    * a validated parent stage recorded on the work units it is handed.

    Each call injects one complete joint vector, runs one trajectory at the
    unit's frozen seed, and returns the 13 target values in the frozen order.
    It never redraws, never substitutes a failure and never writes.
    """

    def __init__(self, plan: nc.FrozenPlan, *, root: Path = ROOT):
        import hm_target_register as tr  # noqa: PLC0415 - heavy, production-only

        self.plan = plan
        config_path = Path(str(plan.require("models.base_config")))
        if not config_path.is_absolute():
            config_path = root / config_path
        if not config_path.exists():
            raise ContractError(
                f"the declared production model config does not exist: {config_path}"
            )
        self.config_path = config_path
        self.base_config = json.loads(config_path.read_text())
        self.config_sha256 = ccs.sha256_file(config_path)

        space_path = Path(str(plan.require("models.m0.calib_space")))
        if not space_path.is_absolute():
            space_path = root / space_path
        self.m0_calib_space = json.loads(space_path.read_text())

        register_path = Path(plan.targets.register_path)
        if not register_path.is_absolute():
            register_path = root / register_path
        register = tr.load_register(register_path)
        by_key = {target.key: target for target in register.targets}
        missing = [key for key in plan.targets.frozen_order if key not in by_key]
        if missing:
            raise ContractError(
                f"the frozen target register does not define {missing}; the production "
                "evaluator will not run against a shorter target vector than the contract"
            )
        self.targets = [by_key[key] for key in plan.targets.frozen_order]
        self.start_year = min(int(t.year) for t in self.targets)
        self.end_year = max(int(t.year) for t in self.targets)

    def calib_space_for(self, model: str) -> dict:
        """The calibration space this model injects through.

        M1 is *generated* from M0 plus the signed ``p`` range rather than read
        from a second file, which is what guarantees the injected M1 vector is
        M0 plus one coordinate and nothing else.
        """
        if model == nc.MODEL_M0:
            return self.m0_calib_space
        return nc.build_m1_calib_space(
            self.m0_calib_space,
            p_name=str(self.plan.require("p_parameter.name")),
            p_config_path=str(self.plan.require("p_parameter.config_path")),
            p_min=float(self.plan.require("p_parameter.min")),
            p_max=float(self.plan.require("p_parameter.max")),
        )

    def config_for(self, unit: ne.WorkUnit, params: Mapping[str, float]) -> dict:
        """The injected config for one work unit.  No simulation yet.

        Separated from :meth:`__call__` so the verification suite can assert
        that the right value lands at the right config path -- including the
        ``p`` path, whose silent-discard failure mode is why it is a signed
        decision -- without running a trajectory.
        """
        from run_lhs_calibration import inject_params  # noqa: PLC0415

        config = inject_params(
            self.base_config,
            {name: float(value) for name, value in params.items()},
            self.calib_space_for(unit.model),
        )
        if unit.model == nc.MODEL_M1:
            # M1 pins the routine hazard scale; without it the engine's p=0
            # identity dispatch does not hold and M1 would not nest M0.
            nc.assert_m1_identity_preconditions(
                routine_hazard_scale=float(self.plan.require(
                    "p_parameter.routine_hazard_scale_identity_value")),
                required_value=1.0,
            )
        return config

    def __call__(
        self, unit: ne.WorkUnit, params: Mapping[str, float], targets: Sequence[str]
    ) -> tuple[float, ...]:
        import contextlib  # noqa: PLC0415
        import io  # noqa: PLC0415

        import hm_target_register as tr  # noqa: PLC0415
        from calib_extractor import extract_annual_outputs  # noqa: PLC0415
        from run_v3 import SimulationEngineV3  # noqa: PLC0415

        if tuple(targets) != self.plan.targets.frozen_order:
            raise ContractError(
                "the production evaluator was handed a target order that is not the frozen "
                "contract; a reordered target vector is fatal, not silently corrected"
            )
        config = self.config_for(unit, params)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            engine = SimulationEngineV3(config, seed=int(unit.seed))
            result = engine.run(run_number=1)
        outputs = extract_annual_outputs(result, self.start_year, self.end_year)
        row: dict[str, float] = {}
        for indicator, series in outputs.items():
            for year, value in series.items():
                row[f"{indicator}_{int(year)}"] = float(value)
        simulated, resolved, unresolved = tr.resolve_simulated(
            self.targets, pd.DataFrame([row])
        )
        if unresolved:
            raise ContractError(
                f"the simulator did not produce {[u['target_key'] for u in unresolved]}; "
                "a target that cannot be resolved is fatal rather than dropped"
            )
        self.plan.targets.assert_order(
            [t.key for t in resolved], what="production evaluator outputs"
        )
        return tuple(float(simulated[key][0]) for key in self.plan.targets.frozen_order)


def production_evaluator(
    unit: ne.WorkUnit, params: Mapping[str, float], targets: Sequence[str]
) -> tuple[float, ...]:
    """Refusal stub.  The real adapter is :class:`ProductionEvaluator`.

    This exists so that the module-level ``EVALUATORS`` table cannot hand out a
    working production callable without going through
    :func:`resolve_evaluator`, which is where the priced-budget
    acknowledgement and the plan are checked.  Reaching this function means a
    caller bypassed that gate.
    """
    raise ContractError(
        "the production evaluator must be constructed through resolve_evaluator(), which "
        "checks the signed plan, the declared production config, the frozen target "
        "register and the priced-budget acknowledgement. Reaching this stub means that "
        "gate was bypassed."
    )


EVALUATORS: dict[str, Callable] = {
    "surrogate": surrogate_evaluator,
    # Built per model space, because the field is defined on normalised
    # coordinates; resolved in resolve_evaluator rather than stored here.
    "surrogate_islands": None,
    "production": production_evaluator,
}


def resolve_evaluator(
    name: str,
    plan: nc.FrozenPlan,
    *,
    priced: bool,
    ledger: ne.Ledger,
) -> Callable:
    """The single gate between a frozen design and spent production compute.

    ``surrogate`` runs no engine and is what the verification suite uses.
    ``production`` is refused unless the budget has been explicitly priced and
    the frozen work carries a validated parent, in addition to the signed plan
    that :meth:`nroy_contract.FrozenPlan.load` has already insisted on.

    There is deliberately **no default**: ``--evaluator`` is a required
    argument, so no command line can reach the simulator by omission.
    """
    if name not in EVALUATORS:
        raise ContractError(f"unknown evaluator {name!r}; choose from {sorted(EVALUATORS)}")
    if name == "surrogate_islands":
        return _island_surrogate(_model_spaces(plan)[str(ledger.model)])
    if name != "production":
        return EVALUATORS[name]
    if not priced:
        raise ContractError(
            "refusing to launch the production evaluator without "
            "--i-have-priced-the-budget. The production specification requires a measured "
            "pilot and a priced trajectory budget first, and launch attempts consume "
            "budget that is never refunded."
        )
    stage = ledger.stage
    if nc.STAGE_PARENT.get(stage) not in (None, nc.STAGE_PREPARE):
        unparented = sorted({
            unit.design_id for unit in ledger.units if not unit.parent_manifest_sha256
        })
        if unparented:
            raise ContractError(
                f"stage {stage!r} has {len(unparented)} work unit(s) with no parent-manifest "
                "hash, so their parent artifacts were never validated. Re-prepare with "
                "--parent-dir before spending production compute."
            )
    return ProductionEvaluator(plan)


def command_evaluate(args: argparse.Namespace) -> int:
    stage_dir = _stage_directory(args.stage_dir)
    manifest = json.loads((stage_dir / "manifest.json").read_text())
    plan = nc.FrozenPlan.load(args.plan or manifest["contract"]["plan_path"])
    ledger = ne.Ledger.load(stage_dir)
    targets = plan.targets

    evaluate = resolve_evaluator(
        args.evaluator, plan,
        priced=bool(args.i_have_priced_the_budget), ledger=ledger,
    )

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

    # The declared wall-clock cap. It stops DISPATCH and lets the unit already
    # in flight finish; the remaining units stay incomplete, which the ordinary
    # resume path turns into PARTIAL. No new status, no shortcut.
    cap = ne.WallClockCap(
        cap_seconds=float(
            args.wall_time_cap_seconds
            if args.wall_time_cap_seconds is not None
            else plan.require("budget.wall_time_cap_seconds")
        )
    )

    results: list[ne.SeedResult] = []
    for position, unit in enumerate(assigned):
        if unit.key in done:
            continue  # a completed work unit is never rerun
        if not cap.may_start():
            cap.record_withheld(
                sum(1 for later in assigned[position:] if later.key not in done)
            )
            break
        cap.record_dispatch()
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

    # These are the numerator and denominator of every implausibility, so a
    # merge that quietly skipped them would publish a COMPLETE, parentable
    # stage containing no acceptance arithmetic at all. Both are required, and
    # both are validated against the frozen target contract and hashed into
    # the manifest.
    if not (args.observed and args.base_variance):
        raise ContractError(
            "--observed and --base-variance are both required: they are the observed "
            "target values z_i and the frozen non-process variance B_i, i.e. the "
            "numerator and denominator of every implausibility. Refusing to merge a "
            "stage with no acceptance arithmetic."
        )
    observed = json.loads(Path(args.observed).read_text())
    base_variance = json.loads(Path(args.base_variance).read_text())
    plan.targets.assert_order(
        sorted(observed, key=lambda name: list(plan.targets.frozen_order).index(name))
        if set(observed) == set(plan.targets.frozen_order) else list(observed),
        what="--observed",
    )
    plan.targets.assert_order(
        sorted(base_variance, key=lambda name: list(plan.targets.frozen_order).index(name))
        if set(base_variance) == set(plan.targets.frozen_order) else list(base_variance),
        what="--base-variance",
    )
    bad = sorted(k for k, v in base_variance.items() if not float(v) > 0.0)
    if bad:
        raise ContractError(f"--base-variance must be strictly positive; non-positive for {bad}")
    target_inputs_sha256 = {
        "observed": ccs.sha256_file(args.observed),
        "base_variance": ccs.sha256_file(args.base_variance),
    }
    # The complete acceptance-input identity, and the comparison that closes
    # the cross-stage hole: a child merged against different observed values or
    # different base variances than its confirmation parent would publish
    # support labels and edge verdicts on a different acceptance basis, and
    # nothing used to notice. The parent's identity was frozen at prepare.
    identity = plan.acceptance_identity(
        observed_sha256=target_inputs_sha256["observed"],
        base_variance_sha256=target_inputs_sha256["base_variance"],
    )
    frozen_parent = _frozen_parent_acceptance_identity(stage_dir)
    if frozen_parent is not None:
        nc.assert_acceptance_identity(
            frozen_parent, identity,
            what=f"merge {stage_dir}: these acceptance inputs against the frozen parent",
        )
    scoring_note = ""
    if seed_frame.empty:
        # No result exists to score. That is only ever legitimate for a stage
        # that did not complete -- a wall-clock cap or an exhausted budget --
        # and such a stage is PARTIAL and cannot become a parent. A COMPLETE
        # stage must carry acceptance arithmetic, so refuse that combination
        # rather than publish a parentable stage with no scores.
        if complete:
            raise ContractError(
                "the merge found no seed-level rows yet the ledger reports every unit "
                "complete; refusing to publish a COMPLETE stage with no acceptance "
                "arithmetic"
            )
        scoring_note = (
            "no seed-level rows exist, so no candidate could be scored. The stage is "
            f"{resume.status} and cannot become a parent. Every work unit is "
            "'work_not_run', which is execution bookkeeping, not a finding."
        )
        print(f"no scores recomputed: {scoring_note}")
    else:
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
        parent_manifest=None,
        extra={
            **(manifest.get("extra") or {}),
            "resume": resume.as_row(),
            "target_inputs_sha256": target_inputs_sha256,
            "acceptance_input_identity": identity,
            **({"scores_not_recomputed_reason": scoring_note} if scoring_note else {}),
        },
        overwrite_completed=True,
    )
    print(f"merged {len(seed_frame)} seed-level row(s); stage status {resume.status}")
    if resume.status != nc.STATUS_COMPLETE:
        print(f"  {resume.reason}")
        print("  a PARTIAL stage cannot become a parent for the next stage")
    return 0


# ==========================================================================
# identity-gate: the M0/M1 pre-production command
# ==========================================================================

def command_identity_gate(args: argparse.Namespace) -> int:
    """Run the M0/M1-at-``p=0`` identity gate and write its artifact.

    **This command runs the engine on purpose.**  It is the one place in this
    stage that does, and it is not a pytest: the identity is a production
    prerequisite, and every pytest that checks it is skipped under the
    repository's no-engine guard, so a green full suite does not establish it.

    The runs are short truncated trajectories (about a second each), not
    production simulations: no calibration, no scenario and no PSA work is
    dispatched, and nothing is written to ``outputs/``.
    """
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print("M0/M1 identity gate -- this runs the real engine on short truncated runs.")
    print(f"  base config: {args.base_config}  end year: {args.end_year}")
    print(f"  seeds:       {list(args.seeds)}")
    print()
    artifact = nig.run_gate(
        base_config=args.base_config,
        end_year=int(args.end_year),
        seeds=[int(s) for s in args.seeds],
    )
    path = ne.atomic_write_json(
        out / nig.GATE_FILENAME, artifact, overwrite_completed=True
    )
    for check in artifact["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        print(f"  [{status}] {check['check']}: {check['detail']}")
    print()
    print(f"wrote {path}")
    if artifact["passed"]:
        print(f"GATE PASSED ({artifact['n_passed']}/{artifact['n_checks']} checks) in "
              f"{artifact['elapsed_seconds']:.1f}s")
        print("This is a prerequisite for launching M1, not an authorisation to run one.")
        return 0
    print(f"GATE FAILED ({artifact['n_passed']}/{artifact['n_checks']} checks)")
    print("No M1 design may be prepared.")
    return 5


# ==========================================================================
# classify: local support and p coverage
# ==========================================================================

def _stage_context(
    args: argparse.Namespace,
) -> tuple[Path, dict, nc.FrozenPlan, nc.ModelSpace, ne.Ledger]:
    stage_dir = _stage_directory(args.stage_dir)
    manifest = json.loads((stage_dir / "manifest.json").read_text())
    plan = nc.FrozenPlan.load(args.plan or manifest["contract"]["plan_path"])
    model = _model_spaces(plan)[str(manifest["model"])]
    return stage_dir, manifest, plan, model, ne.Ledger.load(stage_dir)


def _scores_if_present(stage_dir: Path) -> pd.DataFrame | None:
    path = stage_dir / "candidate_scores.csv"
    return ne.read_frame_exact(path) if path.exists() else None


def command_classify(args: argparse.Namespace) -> int:
    """Turn merged rows into local-support labels and the ``p`` profile.

    Runs after ``merge`` on a local-design stage.  It writes
    ``design_evidence.csv``, ``local_support.csv`` and, for M1,
    ``p_coverage.csv``.  No simulation, no engine: this is classification of
    results that already exist.
    """
    stage_dir, manifest, plan, model, ledger = _stage_context(args)
    scores = _scores_if_present(stage_dir)
    if scores is None:
        raise ContractError(
            f"{stage_dir} has no candidate_scores.csv, so no acceptance decision exists "
            "to classify. Run 'merge' with --observed and --base-variance first."
        )
    # Before any label is written: these scores must have been produced on the
    # same acceptance basis as the parent they descend from, and on the one the
    # current plan describes.
    identity = _assert_acceptance_basis(
        stage_dir, manifest, plan, what="classification"
    )
    # A classification is a completed artifact, and a child stage may already
    # have validated this stage's artifact index against it. Silently
    # reclassifying would leave that child's recorded parent hash describing a
    # file that no longer exists, which is the stale-parent case the handoff
    # contract exists to prevent. Refuse unless the caller says so explicitly.
    if (stage_dir / "local_support.csv").exists() and not args.reclassify:
        raise ContractError(
            f"{stage_dir} already carries local_support.csv. A classification is a "
            "completed artifact, and a child stage may already have validated this "
            "stage's artifact index against it. Pass --reclassify to replace it "
            "deliberately, and re-prepare any stage that already descends from this one."
        )
    evidence = nas.evidence_by_design(ledger, scores)
    ne.atomic_write_frame(
        stage_dir / "design_evidence.csv", nas.evidence_table(evidence),
        overwrite_completed=True,
    )

    anchors = ne.read_frame_exact(stage_dir / "anchors.csv")
    proposals = ne.read_frame_exact(stage_dir / "design_proposals.csv")
    vectors = ne.read_frame_exact(stage_dir / "design_vectors.csv")
    outcomes_path = stage_dir / "kernel_outcomes.csv"
    outcomes = ne.read_frame_exact(outcomes_path) if outcomes_path.exists() else None

    support = nas.local_support_table(
        anchors=anchors, proposals=proposals, design_vectors=vectors,
        kernel_outcomes=outcomes, evidence=evidence, model=model,
        smaller_ambient_scale=plan.smaller_ambient_scale(),
        min_confirming=int(plan.require("support.min_confirming_neighbours")),
        min_direction_classes=int(plan.require("separation.min_direction_classes")),
        scores=scores,
    )
    ne.atomic_write_frame(
        stage_dir / "local_support.csv", support, overwrite_completed=True
    )

    written = ["design_evidence.csv", "local_support.csv"]
    if model.p_name is not None:
        coverage = nas.p_coverage_from_tables(
            bins=[float(b) for b in plan.require("p_parameter.bins")],
            design_vectors=vectors, evidence=evidence, model=model,
        )
        ne.atomic_write_frame(
            stage_dir / "p_coverage.csv", coverage, overwrite_completed=True
        )
        written.append("p_coverage.csv")

    ne.write_stage_manifest(
        stage_dir, stage=manifest["stage"], model=manifest["model"],
        status=str(manifest["status"]), ledger=ledger,
        contract=plan.manifest_contract(), parent_manifest=None,
        extra={
            **(manifest.get("extra") or {}),
            "acceptance_input_identity": identity,
            "local_support_counts": {
                label: int((support["local_support_label"] == label).sum())
                for label in nc.LOCAL_SUPPORT_LABELS
            },
            "n_eligible_for_representation": int(
                support["eligible_for_representation"].astype(bool).sum()
            ),
        },
        overwrite_completed=True,
    )
    print(f"classified {len(support)} anchor(s) in {stage_dir}")
    for label in nc.LOCAL_SUPPORT_LABELS:
        print(f"  {label}: {int((support['local_support_label'] == label).sum())}")
    print(f"  wrote: {', '.join(written)}")
    print("\nThese are forecast-ensemble eligibility labels, not epidemiological "
          "acceptance, and 'no local support detected' is a statement about the "
          "declared finite design.")
    return 0


# ==========================================================================
# regions: edge verdicts, components, representatives
# ==========================================================================

def command_regions(args: argparse.Namespace) -> int:
    """Evaluate the frozen bridges and select representatives on geometry.

    Reads a **completed** bridges stage, labels each frozen minimal edge from
    its bridge probes' evidence, groups the nodes by sampled-path-supported
    edges only, and selects representatives from eligible members using
    normalised geometry alone.  Writes its own regions stage directory with a
    validated parent, so lineage runs unbroken from discovery to regions.

    Runs no simulation: every number comes from the bridges stage's own
    consolidated tables.
    """
    parent_dir = _stage_directory(args.parent_dir)
    parent_manifest = json.loads((parent_dir / "manifest.json").read_text())
    plan = nc.FrozenPlan.load(args.plan or parent_manifest["contract"]["plan_path"])
    model = _model_spaces(plan)[str(parent_manifest["model"])]

    validated = ne.validate_parent_stage(
        parent_dir, expected_stage=nc.STAGE_BRIDGES, expected_model=model.model,
        targets=plan.targets, seeds=plan.seeds, child_stage=nc.STAGE_REGIONS,
    )
    ledger = ne.Ledger.load(parent_dir)
    scores = _scores_if_present(parent_dir)
    if scores is None:
        raise ContractError(
            f"{parent_dir} has no candidate_scores.csv; bridge probes must be merged and "
            "scored before any edge can be labelled"
        )
    # Before the first edge verdict: the bridges stage must have been scored on
    # the same acceptance basis as the confirmation stage whose local assessment
    # the graph's nodes carry. Otherwise an edge could be "sampled-path
    # supported" under one acceptance rule while its endpoints were assessed
    # under another, and the region report would mix the two silently.
    identity = _assert_acceptance_basis(
        parent_dir, parent_manifest, plan, what="edge verdicts"
    )
    nodes = ne.read_frame_exact(parent_dir / "graph_nodes.csv")
    edges_frame = ne.read_frame_exact(parent_dir / "candidate_edges.csv")
    proposals = ne.read_frame_exact(parent_dir / "design_proposals.csv")

    evidence = nas.evidence_by_design(ledger, scores)
    minimal = nas.minimal_edges_from_table(edges_frame)
    verdicts = nas.edge_verdict_table(
        edges=minimal, nodes=nodes, proposals=proposals, evidence=evidence,
        n_declared=len(plan.bridge_fractions()),
        pass_rule=str(plan.require("graph.bridge_pass_rule")),
    )
    node_ids = sorted(str(value) for value in nodes["candidate_id"])
    groups = ng.components(node_ids, verdicts)
    # Decision 7 and spec sec. 1 both read "local/REGION support". Region
    # support is what the graph exists to establish, so eligibility is
    # local-OR-region and the ground is recorded per candidate rather than
    # collapsed into a bare flag.
    eligible, ground = nas.representation_eligibility(nodes, groups)
    representatives = ng.select_representatives(
        groups, nas.node_geometry(nodes),
        n_per_component=int(plan.require("representative_selection.n_per_component")),
        rule=str(plan.require("representative_selection.rule")),
        eligible=eligible,
    )
    if not representatives.empty:
        representatives["eligibility_ground"] = [
            ground.get(str(value), "") if pd.notna(value) else ""
            for value in representatives["candidate_id"]
        ]
    local_only = nas.node_eligibility(nodes)
    region_only = nas.region_support(nodes, groups)
    labels = {
        str(row["candidate_id"]): str(row["local_support_label"])
        for _, row in nodes.iterrows()
    }
    eligibility = pd.DataFrame([
        {
            "candidate_id": node,
            "local_support_label": labels[node],
            "has_local_support": bool(local_only.get(node, False)),
            "has_region_support": bool(region_only.get(node, False)),
            "eligible_for_representation": bool(eligible[node]),
            "eligibility_ground": ground[node],
        }
        for node in sorted(eligible)
    ])
    blocking = ng.unresolved_changes_representation(groups, verdicts)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tables = ng.graph_tables(nas.edges_from_table(edges_frame), minimal, verdicts, groups)
    ne.atomic_write_frame(out / "candidate_edges.csv", tables["candidate_edges"])
    ne.atomic_write_frame(out / "edge_verdicts.csv", tables["edge_verdicts"])
    ne.atomic_write_frame(out / "components.csv", tables["components"])
    ne.atomic_write_frame(out / "representatives.csv", representatives)
    ne.atomic_write_frame(out / "representation_eligibility.csv", eligibility)
    ne.atomic_write_frame(out / "graph_nodes.csv", nodes)
    # Carried forward unchanged, not rebuilt: the regions package must report
    # the same local assessment the graph was built from.
    support_path = parent_dir / "local_support.csv"
    if support_path.exists():
        ne.atomic_write_frame(
            out / "local_support.csv", ne.read_frame_exact(support_path)
        )
    ne.atomic_write_json(out / "unresolved_blocking_edges.json", {
        "edge_ids": blocking,
        "meaning": (
            "unresolved edges that would merge two components if resolved, and therefore "
            "the only ones that can change which groups receive representatives"
        ),
        "escalation": (
            "these are the recorded unresolved geometry questions that justify a bounded "
            "archive-informed path search, and the only trigger for considering an "
            "emulator (spec sec. 4 and sec. 6). An emulator may never rule out a region "
            "or declare plausibility without simulator evaluation."
        ),
    })

    # A regions stage runs no simulation, so it has an empty frozen work list
    # by construction rather than an unfinished one.
    empty = ne.Ledger(stage=nc.STAGE_REGIONS, model=model.model, units=())
    empty.save(out)
    _freeze_parent_acceptance_identity(out, validated, parent_dir=parent_dir)
    ne.write_stage_manifest(
        out, stage=nc.STAGE_REGIONS, model=model.model, status=nc.STATUS_COMPLETE,
        ledger=empty, contract=plan.manifest_contract(),
        parent_manifest=validated,
        extra={
            "acceptance_input_identity": identity,
            "n_nodes": len(node_ids),
            "n_minimal_edges": len(minimal),
            "n_sampled_path_supported_edges": sum(1 for v in verdicts if v.supported),
            "n_unresolved_edges": sum(1 for v in verdicts if not v.supported),
            "n_components": len(groups),
            "n_representatives": int(representatives["candidate_id"].notna().sum())
            if not representatives.empty else 0,
            "eligibility_grounds": {
                ground_name: int((eligibility["eligibility_ground"] == ground_name).sum())
                for ground_name in sorted(set(eligibility["eligibility_ground"]))
            },
            "eligibility_rule": (
                "local OR region support, as production decision 7 and spec sec. 1 both "
                "state ('local/region support'). Region support means membership of a "
                "component held together by at least one sampled-path-supported edge; a "
                "singleton has none, so its only route is its own local support."
            ),
            "unresolved_edges_that_would_change_representation": blocking,
            "limitation": (
                "'sampled-path supported' is discrete evidence at the declared "
                "interpolation fractions; it is not continuous connectivity, and a "
                "component here is not a topological component of the NROY set"
            ),
        },
    )
    print(f"REGIONS {model.model} in {out}")
    print(f"  nodes:                {len(node_ids)}")
    print(f"  minimal edges:        {len(minimal)}")
    print(f"  sampled-path supported: {sum(1 for v in verdicts if v.supported)}")
    print(f"  unresolved edges:     {sum(1 for v in verdicts if not v.supported)}")
    print(f"  components:           {len(groups)}")
    if blocking:
        print(f"  {len(blocking)} unresolved edge(s) WOULD change representation: {blocking}")
        print("  that is a recorded unresolved geometry question (spec sec. 4)")
    return 0


# ==========================================================================
# diagnose: the secondary correlated-output diagnostic (spec sec. 7)
# ==========================================================================

def command_diagnose(args: argparse.Namespace) -> int:
    """Run the correlated-output diagnostic.  It cannot change acceptance.

    Disabled unless the plan switches it on, and its numbers become required
    decisions exactly when it is switched on
    (:data:`nroy_contract.CONDITIONAL_DECISIONS`).  The result is written to
    its own file and is never merged into ``candidate_scores``.
    """
    stage_dir, manifest, plan, _model, _ledger = _stage_context(args)
    if not bool(plan.get("correlated_output_diagnostic.enabled", False)):
        raise ContractError(
            "correlated_output_diagnostic.enabled is false in the run plan. The diagnostic "
            "is secondary and off by default; enabling it also makes its shrinkage, "
            "bootstrap and stability decisions required, so it cannot run on defaults."
        )
    seed_path = stage_dir / "seed_results.csv"
    if not seed_path.exists():
        raise ContractError(
            f"{stage_dir} has no seed_results.csv; the diagnostic uses confirmation "
            "replicates and will not be reconstructed from candidate summaries"
        )
    diagnostic = nas.correlated_output_diagnostic(
        ne.read_frame_exact(seed_path),
        targets=plan.targets,
        shrinkage=float(plan.require("correlated_output.shrinkage")),
        n_bootstrap=int(plan.require("correlated_output.n_seed_block_bootstrap")),
        bootstrap_seed=int(plan.require("correlated_output.bootstrap_seed")),
        rank_tolerance=float(plan.require("correlated_output.stability_rank_tolerance")),
        direction_cosine=float(plan.require("correlated_output.stability_direction_cosine")),
        scores=_scores_if_present(stage_dir),
    )
    payload = {
        **diagnostic.as_dict(),
        "stage": manifest["stage"],
        "model": manifest["model"],
        "leading_directions": [list(d) for d in diagnostic.leading_directions],
        "target_order": list(plan.targets.frozen_order),
    }
    ne.atomic_write_json(
        stage_dir / "correlated_output_diagnostic.json", payload,
        overwrite_completed=True,
    )
    print(f"correlated-output diagnostic: {diagnostic.status}")
    print(f"  {diagnostic.reason}")
    print(f"  seed blocks (effective stochastic sample size): {diagnostic.n_seed_blocks}")
    print(f"  effective rank: {diagnostic.effective_rank}")
    print(f"  rank correlation with pointwise I_max: "
          f"{diagnostic.rank_correlation_with_i_max}")
    print("\nSecondary by construction: this diagnostic carries no pass flag and cannot "
          "change the primary set.")
    return 0 if diagnostic.status == nas.DIAGNOSTIC_AVAILABLE else 4


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
        "design_evidence", "graph_nodes",
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
    p.add_argument("--anchors", default="",
                   help="CSV of complete joint confirmed vectors; required for every "
                        "stage except 'bridges', whose node set comes from its parent")
    p.add_argument("--archive", default="",
                   help="CSV of complete joint archive vectors (with an optional 'j' "
                        "column) for the declared low-J correlation-following source")
    p.add_argument("--model", choices=list(nc.MODELS), default=nc.MODEL_M0)
    p.add_argument("--stage", choices=list(nc.STAGE_ORDER), default=nc.STAGE_DISCOVERY)
    p.add_argument("--parent-dir", default="",
                   help="the COMPLETE parent stage directory; required for any stage "
                        "after 'prepare'")
    p.add_argument("--parent-manifest-sha256", default="",
                   help="optional: assert the validated parent's manifest hash")
    p.add_argument("--identity-gate", default="",
                   help=f"path to a passing {nig.GATE_FILENAME}; REQUIRED for "
                        "--model M1, because the pytests that check the identity are "
                        "skipped under the no-engine guard")
    p.set_defaults(func=command_prepare)

    p = sub.add_parser(
        "identity-gate",
        help="run the M0/M1-at-p=0 identity gate (RUNS THE ENGINE; required before M1)",
    )
    p.add_argument("--out", required=True, help="directory to write the gate artifact")
    p.add_argument("--base-config", default=nig.DEFAULT_BASE_CONFIG)
    p.add_argument("--end-year", type=int, default=nig.DEFAULT_END_YEAR)
    p.add_argument("--seeds", type=int, nargs="+", default=list(nig.DEFAULT_SEEDS))
    p.set_defaults(func=command_identity_gate)

    p = sub.add_parser("evaluate", help="worker: evaluate frozen rows of one shard")
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--of", type=int, required=True)
    p.add_argument("--evaluator", choices=sorted(EVALUATORS), required=True)
    p.add_argument("--i-have-priced-the-budget", action="store_true")
    p.add_argument("--wall-time-cap-seconds", type=float, default=None,
                   help="override the plan's declared wall-clock cap for this worker; "
                        "the cap stops dispatch and lets in-flight work finish")
    p.set_defaults(func=command_evaluate)

    p = sub.add_parser("merge", help="validate and consolidate shard outputs")
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.add_argument("--observed", default=None, help="JSON mapping target -> observed value")
    p.add_argument("--base-variance", default=None, help="JSON mapping target -> frozen B_i")
    p.set_defaults(func=command_merge)

    p = sub.add_parser("classify", help="local-support labels and the p coverage profile")
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.add_argument("--reclassify", action="store_true",
                   help="deliberately replace an existing classification; any stage "
                        "already descending from this one must be re-prepared")
    p.set_defaults(func=command_classify)

    p = sub.add_parser("regions", help="edge verdicts, components and representatives")
    p.add_argument("--plan", default=None)
    p.add_argument("--parent-dir", required=True,
                   help="the COMPLETE bridges stage directory")
    p.add_argument("--out", required=True, help="the regions stage directory to write")
    p.set_defaults(func=command_regions)

    p = sub.add_parser(
        "diagnose",
        help="secondary correlated-output diagnostic; cannot change acceptance",
    )
    p.add_argument("--plan", default=None)
    p.add_argument("--stage-dir", required=True)
    p.set_defaults(func=command_diagnose)

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
