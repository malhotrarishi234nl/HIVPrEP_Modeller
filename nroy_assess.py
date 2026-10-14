#!/usr/bin/env python3
"""Assessment stages of the robust-NROY freeze: the step from rows to labels.

:mod:`nroy_contract` holds the classification *rules*, :mod:`nroy_graph` the
connectivity *geometry* and :mod:`nroy_execution` the ledger and merge gate.
This module is the glue that was missing: it turns the consolidated tables a
merged stage leaves on disk into the five evidence states, the four
local-support labels, the ``p`` coverage profile, the candidate-edge graph,
the edge verdicts, the components, the representatives and the secondary
correlated-output diagnostic.

Three disciplines are enforced here rather than left to the caller.

* **"Nothing ran" is never "nothing was found".**  Evidence is derived from the
  frozen ledger first and the score table second, so a design with no attempt
  is ``work_not_run`` and a design whose attempts failed is
  ``execution_failure``.  Neither can reach a negative scientific label.
* **Geometry is recomputed, never read back.**  Direction vectors for the
  independent-direction-class requirement are recomputed from the frozen
  design vectors and the anchor, in the normalised metric.  Nothing depends on
  a stored direction that could have drifted from the vector it describes.
* **The diagnostic cannot touch acceptance.**  The correlated-output
  diagnostic of spec sec. 7 returns its own object, is stability-gated, and has
  no path to ``candidate_scores`` or to any pass flag.

It constructs no simulation engine and runs no simulation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

import nroy_contract as nc
import nroy_execution as ne
import nroy_graph as ng
from coupled_calibration_search import ContractError


# ==========================================================================
# Evidence states, derived from the ledger and the recomputed scores
# ==========================================================================

@dataclass(frozen=True)
class DesignEvidence:
    """The resolved evidence state of one design id, with its bookkeeping."""

    design_id: str
    evidence: str
    n_seeds_declared: int
    n_seeds_succeeded: int
    n_failed_attempts: int
    scored: bool
    nominal_pass: bool | None
    i_max: float | None
    reason: str

    @property
    def confirmed(self) -> bool:
        return self.evidence == nc.EVIDENCE_CONFIRMED

    def as_row(self) -> dict:
        return {
            "design_id": self.design_id,
            "evidence_state": self.evidence,
            "n_seeds_declared": self.n_seeds_declared,
            "n_seeds_succeeded": self.n_seeds_succeeded,
            "n_failed_attempts": self.n_failed_attempts,
            "scored": bool(self.scored),
            "nominal_pass": self.nominal_pass,
            "i_max": self.i_max,
            "evidence_reason": self.reason,
        }


def evidence_by_design(
    ledger: ne.Ledger,
    scores: pd.DataFrame | None,
) -> dict[str, DesignEvidence]:
    """Resolve each frozen design id to exactly one of the five states.

    The order of the checks is the contract.  A design is ``confirmed`` or
    ``no_support_detected`` only when **every** declared seed produced a
    successful result *and* the independently recomputed score table carries
    the candidate.  Short of that it is an execution or dispatch statement:

    * no attempt at all                     -> ``work_not_run``
    * attempts made, not all seeds complete -> ``execution_failure``
    * complete but absent from the scores   -> ``unresolved_evidence``

    Acceptance itself is read from ``nominal_pass``, which
    :func:`nroy_execution.recompute_candidate_scores` derives from the
    seed-level rows with the frozen ``I_max <= 3`` rule.  Nothing here
    recomputes or reinterprets acceptance.
    """
    seeds_by_design: dict[str, set[int]] = {}
    for unit in ledger.units:
        seeds_by_design.setdefault(unit.design_id, set()).add(int(unit.seed))

    succeeded: dict[str, set[int]] = {}
    failures: dict[str, int] = {}
    attempted: set[str] = set()
    for attempt in ledger.attempts:
        attempted.add(attempt.design_id)
        if attempt.status == ne.ATTEMPT_SUCCESS:
            succeeded.setdefault(attempt.design_id, set()).add(int(attempt.seed))
        elif attempt.status in (ne.ATTEMPT_FAILED, ne.ATTEMPT_LOST):
            failures[attempt.design_id] = failures.get(attempt.design_id, 0) + 1

    scored: dict[str, tuple[bool, float]] = {}
    if scores is not None and not scores.empty:
        if "nominal_pass" not in scores.columns:
            raise ContractError(
                "the score table has no 'nominal_pass' column, so no acceptance decision "
                "can be read from it; a merge that skipped scoring may not be assessed"
            )
        for _, row in scores.iterrows():
            scored[str(row["design_id"])] = (
                bool(row["nominal_pass"]), float(row["i_max"])
            )

    out: dict[str, DesignEvidence] = {}
    for design_id in sorted(seeds_by_design):
        declared = seeds_by_design[design_id]
        done = succeeded.get(design_id, set())
        n_failed = failures.get(design_id, 0)

        def build(evidence: str, reason: str) -> DesignEvidence:
            passed, i_max = scored.get(design_id, (None, None))
            return DesignEvidence(
                design_id=design_id,
                evidence=evidence,
                n_seeds_declared=len(declared),
                n_seeds_succeeded=len(done),
                n_failed_attempts=n_failed,
                scored=design_id in scored,
                nominal_pass=passed,
                i_max=i_max,
                reason=reason,
            )

        if design_id not in attempted:
            out[design_id] = build(
                nc.EVIDENCE_NOT_RUN,
                "no execution attempt exists for this frozen work unit; this is dispatch "
                "bookkeeping, not a scientific result",
            )
            continue
        if not declared <= done:
            missing = sorted(declared - done)
            out[design_id] = build(
                nc.EVIDENCE_EXECUTION_FAILURE,
                f"{len(missing)} declared seed(s) have no successful result "
                f"({n_failed} failed attempt(s)); an execution failure is never evidence "
                "about the model",
            )
            continue
        if design_id not in scored:
            out[design_id] = build(
                nc.EVIDENCE_UNRESOLVED,
                "every declared seed completed but the independently recomputed score "
                "table does not carry this candidate, so no acceptance decision exists",
            )
            continue
        passed, _ = scored[design_id]
        out[design_id] = build(
            nc.EVIDENCE_CONFIRMED if passed else nc.EVIDENCE_NO_SUPPORT,
            "nominal I_max is within the frozen cutoff, recomputed from seed-level rows"
            if passed else
            "the complete declared seed block ran and nominal I_max exceeds the frozen "
            "cutoff; this is a finding about this probe, not about the anchor",
        )
    return out


def evidence_table(evidence: Mapping[str, DesignEvidence]) -> pd.DataFrame:
    """Tidy per-design evidence states, for the package and for audits."""
    return pd.DataFrame(
        [evidence[key].as_row() for key in sorted(evidence)]
    )


# ==========================================================================
# Local support
# ==========================================================================

def _anchor_of(parent_ids: Sequence[str]) -> str:
    if not parent_ids:
        raise ContractError("a probe with no parent lineage cannot be attributed to an anchor")
    return str(parent_ids[0])


def neighbour_evidence_for_anchor(
    anchor_id: str,
    *,
    proposals: pd.DataFrame,
    vectors: Mapping[str, Mapping[str, float]],
    anchor_params: Mapping[str, float],
    model: nc.ModelSpace,
    evidence: Mapping[str, DesignEvidence],
) -> list[nc.NeighbourEvidence]:
    """Build the neighbour evidence for one anchor, recomputing directions.

    The move direction is recomputed as ``normalise(probe) - normalise(anchor)``
    from the frozen design vector, so the geometric independence check in
    :func:`nroy_contract.classify_local_support` rests on the vector that was
    actually evaluated rather than on a stored label or a stored direction.
    """
    anchor_u = model.normalise({name: float(anchor_params[name]) for name in model.names})
    out: list[nc.NeighbourEvidence] = []
    for _, row in proposals.iterrows():
        parents = [p for p in str(row["parent_ids"]).split("|") if p]
        if _anchor_of(parents) != anchor_id:
            continue
        kernel = str(row["kernel_id"])
        if kernel not in nc.KERNEL_FAMILIES:
            raise ContractError(f"proposal {row['design_id']} has unknown kernel {kernel!r}")
        design_id = str(row["design_id"])
        state = evidence.get(design_id)
        if state is None:
            # A proposal with no frozen work unit was never dispatched.  Saying
            # so is the point of the fifth evidence state.
            state = DesignEvidence(
                design_id=design_id, evidence=nc.EVIDENCE_NOT_RUN, n_seeds_declared=0,
                n_seeds_succeeded=0, n_failed_attempts=0, scored=False,
                nominal_pass=None, i_max=None,
                reason="the proposal is not present in the frozen ledger",
            )
        params = vectors.get(design_id)
        direction: tuple[float, ...] | None = None
        if params is not None:
            delta = model.normalise(
                {name: float(params[name]) for name in model.names}
            ) - anchor_u
            norm = float(np.linalg.norm(delta))
            if norm > 0.0:
                direction = tuple(float(v) for v in (delta / norm))
        out.append(nc.NeighbourEvidence(
            design_id=design_id,
            kernel=kernel,
            scale=float(row["scale"]),
            direction_class=str(row["direction_class"]),
            vector_hash=str(row["vector_hash"]),
            distance=float(row["distance_to_parent"]),
            evidence=state.evidence,
            confirmed=state.confirmed,
            direction=direction,
        ))
    return out


def local_support_table(
    *,
    anchors: pd.DataFrame,
    proposals: pd.DataFrame,
    design_vectors: pd.DataFrame,
    kernel_outcomes: pd.DataFrame | None,
    evidence: Mapping[str, DesignEvidence],
    model: nc.ModelSpace,
    smaller_ambient_scale: float,
    min_confirming: int,
    min_direction_classes: int,
    scores: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One local-support row per anchor, with every underlying count.

    ``designed_neighbours`` is taken from the *accepted* proposals attributed to
    the anchor, so an anchor whose probes were not all dispatched is reported
    as an incomplete design rather than as a point with no support.

    ``feasible_direction_coverage`` and ``redraw_exhausted`` come from the
    frozen kernel outcomes written at preparation time.  A boundary anchor whose
    directions were mostly infeasible therefore reaches ``unresolved``, which is
    the behaviour spec sec. 9 asks for.

    ``variance_unstable_only`` is derived from the score table: if every
    confirming neighbour of an anchor was promoted only through the variance
    bound, the local label cannot be negative.
    """
    required = {"candidate_id"} | set(model.names)
    missing = sorted(required - set(anchors.columns))
    if missing:
        raise ContractError(f"anchor table is missing column(s) {missing}")

    accepted = proposals
    if "accepted" in proposals.columns:
        accepted = proposals[proposals["accepted"].astype(str).isin(("True", "true", "1"))]

    vectors: dict[str, dict[str, float]] = {}
    for _, row in design_vectors.iterrows():
        vectors[str(row["design_id"])] = {
            name: float(row[name]) for name in model.names if name in design_vectors.columns
        }

    coverage: dict[tuple[str, str], float | None] = {}
    exhausted: dict[str, bool] = {}
    if kernel_outcomes is not None and not kernel_outcomes.empty:
        for _, row in kernel_outcomes.iterrows():
            key = (str(row["anchor_id"]), str(row["kernel_id"]))
            raw = row.get("feasible_direction_coverage")
            value = None
            try:
                number = float(raw)
                value = None if np.isnan(number) else number
            except (TypeError, ValueError):
                value = None
            coverage[key] = value
            if str(row.get("redraw_exhausted", "")) in ("True", "true", "1"):
                exhausted[str(row["anchor_id"])] = True

    unstable: dict[str, bool] = {}
    if scores is not None and not scores.empty and "variance_unstable" in scores.columns:
        for _, row in scores.iterrows():
            unstable[str(row["design_id"])] = bool(row["variance_unstable"])

    rows: list[dict] = []
    for _, anchor_row in anchors.iterrows():
        anchor_id = str(anchor_row["candidate_id"])
        anchor_params = {name: float(anchor_row[name]) for name in model.names}
        neighbours = neighbour_evidence_for_anchor(
            anchor_id, proposals=accepted, vectors=vectors,
            anchor_params=anchor_params, model=model, evidence=evidence,
        )
        designed = len({n.vector_hash for n in neighbours})
        confirming = [n for n in neighbours if n.confirmed]
        variance_unstable_only = bool(confirming) and all(
            unstable.get(n.design_id, False) for n in confirming
        )
        # The ambient kernel is the one whose directions can be infeasible at a
        # bound, so its coverage is the one that bears on isolation.
        ambient_coverage = coverage.get((anchor_id, nc.KERNEL_AMBIENT))
        support = nc.classify_local_support(
            neighbours,
            smaller_ambient_scale=float(smaller_ambient_scale),
            min_confirming=int(min_confirming),
            min_direction_classes=int(min_direction_classes),
            designed_neighbours=designed,
            feasible_direction_coverage=ambient_coverage,
            redraw_exhausted=exhausted.get(anchor_id, False),
            variance_unstable_only=variance_unstable_only,
        )
        rows.append({
            "candidate_id": anchor_id,
            "model": model.model,
            "anchor_vector_hash": model.vector_hash(anchor_params),
            **support.as_row(),
        })
    return pd.DataFrame(rows)


# ==========================================================================
# p coverage
# ==========================================================================

def p_coverage_from_tables(
    *,
    bins: Sequence[float],
    design_vectors: pd.DataFrame,
    evidence: Mapping[str, DesignEvidence],
    model: nc.ModelSpace,
) -> pd.DataFrame:
    """Descriptive ``p`` coverage over the declared bins.

    Only designs with a resolved scientific state contribute: a probe that was
    never run cannot make a bin "evaluated".  Coverage remains descriptive --
    a covered bin is where the design happened to confirm points, not a claim
    that every value in the bin is plausible.
    """
    if model.p_name is None:
        raise ContractError("p coverage requires M1")
    if model.p_name not in design_vectors.columns:
        raise ContractError(
            f"the design vector table has no {model.p_name!r} column; p coverage cannot be "
            "reconstructed from marginals or filenames"
        )
    import nroy_report as nrp  # noqa: PLC0415 - avoids a module-level import cycle

    values: list[float] = []
    confirmed: list[bool] = []
    for _, row in design_vectors.iterrows():
        state = evidence.get(str(row["design_id"]))
        if state is None or state.evidence not in nc.EVIDENCE_SCIENTIFIC:
            continue
        values.append(float(row[model.p_name]))
        confirmed.append(state.confirmed)
    return nrp.p_coverage_table(bins, values, confirmed)


# ==========================================================================
# Graph: nodes, candidate edges, verdicts, components, representatives
# ==========================================================================

def graph_nodes_table(
    *,
    anchors: pd.DataFrame,
    local_support: pd.DataFrame,
    model: nc.ModelSpace,
) -> pd.DataFrame:
    """The declared node set for the graph, with eligibility carried across.

    Every assessed anchor is a node: the graph describes how confirmed points
    relate, and dropping the ineligible ones would hide exactly the structure a
    reader needs to judge the representation decision.  Eligibility travels with
    the node and gates *representative selection*, not graph membership.
    """
    labels = {
        str(row["candidate_id"]): row for _, row in local_support.iterrows()
    }
    missing = sorted(
        str(row["candidate_id"]) for _, row in anchors.iterrows()
        if str(row["candidate_id"]) not in labels
    )
    if missing:
        raise ContractError(
            f"these anchors have no local-support row: {missing}. Run 'classify' on the "
            "parent stage before building the graph; an anchor with no completed local "
            "assessment may not silently enter representative selection."
        )
    rows: list[dict] = []
    for _, anchor_row in anchors.iterrows():
        anchor_id = str(anchor_row["candidate_id"])
        label_row = labels[anchor_id]
        params = {name: float(anchor_row[name]) for name in model.names}
        u = model.normalise(params)
        rows.append({
            "candidate_id": anchor_id,
            "model": model.model,
            "vector_hash": model.vector_hash(params),
            "local_support_label": str(label_row["local_support_label"]),
            "eligible_for_representation": str(
                label_row["eligible_for_representation"]
            ) in ("True", "true", "1"),
            **{f"u:{name}": float(value) for name, value in zip(model.names, u)},
            **params,
        })
    return pd.DataFrame(rows)


def node_geometry(nodes: pd.DataFrame) -> dict[str, list[float]]:
    """Normalised coordinates per node, read from the ``u:`` columns.

    Representative selection sees exactly this and nothing else: there is no
    argument through which a policy outcome could reach it.
    """
    columns = [c for c in nodes.columns if c.startswith("u:")]
    if not columns:
        raise ContractError("node table carries no normalised 'u:' coordinates")
    return {
        str(row["candidate_id"]): [float(row[c]) for c in columns]
        for _, row in nodes.iterrows()
    }


def node_eligibility(nodes: pd.DataFrame) -> dict[str, bool]:
    return {
        str(row["candidate_id"]): str(row["eligible_for_representation"])
        in ("True", "true", "1")
        for _, row in nodes.iterrows()
    }


def edges_from_table(edges: pd.DataFrame) -> list[ng.CandidateEdge]:
    out: list[ng.CandidateEdge] = []
    for _, row in edges.iterrows():
        out.append(ng.CandidateEdge(
            edge_id=str(row["edge_id"]),
            left=str(row["left_candidate_id"]),
            right=str(row["right_candidate_id"]),
            distance=float(row["distance_normalised"]),
            rank=int(row["proposal_rank"]),
        ))
    return out


def minimal_edges_from_table(edges: pd.DataFrame) -> list[ng.CandidateEdge]:
    """The frozen minimal edge set, read back from the prepared table.

    Read back rather than recomputed: the minimal set is a *frozen* choice about
    which bridges to pay for, and recomputing it at verdict time would let a
    later code change silently retest a different set of edges than the one
    whose probes were simulated.
    """
    if "in_minimal_edge_set" not in edges.columns:
        raise ContractError("candidate-edge table does not record the minimal edge set")
    selected = edges[edges["in_minimal_edge_set"].astype(str).isin(("True", "true", "1"))]
    return edges_from_table(selected)


def edge_verdict_table(
    *,
    edges: Sequence[ng.CandidateEdge],
    nodes: pd.DataFrame,
    proposals: pd.DataFrame,
    evidence: Mapping[str, DesignEvidence],
    n_declared: int,
    pass_rule: str,
) -> list[ng.EdgeVerdict]:
    """One verdict per frozen minimal edge, from its bridge probes' evidence.

    Endpoint confirmation is the *local* question: an endpoint counts as
    confirmed only if its local assessment found support, because an edge
    between two points that are not themselves eligible cannot make either one
    eligible.
    """
    eligible = node_eligibility(nodes)
    by_edge: dict[str, list[str]] = {}
    for _, row in proposals.iterrows():
        if str(row["kernel_id"]) != nc.KERNEL_BRIDGE:
            continue
        design_id = str(row["design_id"])
        state = evidence.get(design_id)
        edge_id = str(row.get("edge_id", "")) or _edge_id_of(design_id)
        by_edge.setdefault(edge_id, []).append(
            state.evidence if state is not None else nc.EVIDENCE_NOT_RUN
        )
    return [
        ng.classify_edge(
            edge,
            endpoint_confirmed=eligible,
            probe_evidence=by_edge.get(edge.edge_id, []),
            n_declared=int(n_declared),
            pass_rule=pass_rule,
        )
        for edge in sorted(edges, key=lambda e: e.edge_id)
    ]


def _edge_id_of(design_id: str) -> str:
    """Recover the edge id from a bridge design id.

    Bridge design ids are ``stage/model/bridge/<edge_id>/<fraction>``, and the
    edge id itself contains ``--`` rather than ``/``, so the fourth field is
    the edge id exactly.
    """
    parts = str(design_id).split("/")
    if len(parts) < 5 or parts[2] != nc.KERNEL_BRIDGE:
        raise ContractError(
            f"{design_id!r} is not a bridge design id, so no edge can be attributed to it"
        )
    return parts[3]


# ==========================================================================
# Correlated-output diagnostic (spec sec. 7) -- secondary, cannot change
# the primary set, and unavailable unless it is stable
# ==========================================================================

DIAGNOSTIC_AVAILABLE = "available"
DIAGNOSTIC_UNSTABLE = "unavailable_unstable"
DIAGNOSTIC_INSUFFICIENT = "unavailable_insufficient_seed_blocks"


@dataclass(frozen=True)
class CorrelatedOutputDiagnostic:
    """A correlated **stochastic-residual** diagnostic, explicitly not more.

    Observation and model-discrepancy covariance are not invented here, so this
    is not a multivariate acceptance calculation and the class name does not
    pretend otherwise.  It carries no pass flag and no implausibility, by
    construction: there is no field through which it could change the primary
    set.
    """

    status: str
    reason: str
    n_candidates: int
    n_seed_blocks: int
    effective_rank: float | None
    condition_number: float | None
    leading_directions: tuple[tuple[float, ...], ...]
    shrinkage: float
    rank_spread_bootstrap: float | None
    rank_spread_leave_one_out: float | None
    min_direction_cosine: float | None
    rank_correlation_with_i_max: float | None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "is_secondary": True,
            "can_change_primary_acceptance": False,
            "interpretation": (
                "correlated stochastic-residual diagnostic; observation and "
                "model-discrepancy covariance are not invented, so this is NOT a "
                "multivariate acceptance calculation"
            ),
            "n_candidates": self.n_candidates,
            "n_seed_blocks": self.n_seed_blocks,
            "effective_stochastic_sample_size": self.n_seed_blocks,
            "effective_sample_size_note": (
                "the number of independent seed blocks, NOT candidates times seeds"
            ),
            "effective_rank": self.effective_rank,
            "condition_number": self.condition_number,
            "n_leading_directions": len(self.leading_directions),
            "shrinkage": self.shrinkage,
            "rank_spread_bootstrap": self.rank_spread_bootstrap,
            "rank_spread_leave_one_candidate_out": self.rank_spread_leave_one_out,
            "min_leading_direction_cosine": self.min_direction_cosine,
            "rank_correlation_with_pointwise_i_max": self.rank_correlation_with_i_max,
        }


def _shrunk_covariance(residuals: np.ndarray, shrinkage: float) -> np.ndarray:
    """Candidate-centred covariance shrunk toward its own diagonal.

    Shrinkage toward the diagonal, not toward the identity: the targets are on
    wildly different scales, so an identity target would be a statement about
    units rather than about correlation.
    """
    lam = float(shrinkage)
    if not (0.0 <= lam <= 1.0):
        raise ContractError(f"correlated_output.shrinkage must lie in [0, 1]; got {lam}")
    n = residuals.shape[0]
    if n < 2:
        raise ContractError("a covariance needs at least two centred rows")
    sample = np.cov(residuals, rowvar=False, ddof=1)
    sample = np.atleast_2d(sample)
    return (1.0 - lam) * sample + lam * np.diag(np.diag(sample))


def _effective_rank(covariance: np.ndarray) -> tuple[float, float, np.ndarray]:
    """Participation-ratio effective rank, condition number and eigenvectors.

    The effective rank is ``(sum l_i)^2 / sum l_i^2`` on the non-negative
    eigenvalues: a continuous measure that does not need an arbitrary
    "eigenvalue is zero" threshold, which is the kind of undeclared numerical
    choice this stage is built to avoid.
    """
    values, vectors = np.linalg.eigh(covariance)
    values = np.clip(values, 0.0, None)
    total = float(values.sum())
    if total <= 0.0:
        return 0.0, float("inf"), vectors
    rank = float(total ** 2 / float((values ** 2).sum()))
    positive = values[values > 0.0]
    condition = (
        float(positive.max() / positive.min()) if positive.size else float("inf")
    )
    return rank, condition, vectors[:, np.argsort(values)[::-1]]


def correlated_output_diagnostic(
    seed_frame: pd.DataFrame,
    *,
    targets: nc.TargetContract,
    shrinkage: float,
    n_bootstrap: int,
    bootstrap_seed: int,
    rank_tolerance: float,
    direction_cosine: float,
    scores: pd.DataFrame | None = None,
    n_directions: int = 2,
) -> CorrelatedOutputDiagnostic:
    """Candidate-specific stochastic covariance from confirmation replicates.

    Follows spec sec. 7 in the order the section states it:

    1. centre outputs **within** candidate, so between-candidate structure
       cannot masquerade as stochastic correlation;
    2. estimate covariance with the declared shrinkage;
    3. report effective rank, condition number and dominant directions;
    4. treat shared CRN seed ids as **blocks**.  The effective stochastic
       sample size is the number of seed blocks, not candidates times seeds;
       naive pooling that creates pseudo-replicates is refused by construction,
       because every resample below resamples whole seed blocks;
    5. gate on seed-block bootstrap and leave-one-candidate-out stability, and
       report ``unavailable_unstable`` otherwise.  The diagnostic is never
       repaired by dropping targets or switching estimator.
    """
    columns = [f"target:{name}" for name in targets.frozen_order]
    missing = [c for c in columns if c not in seed_frame.columns]
    if missing:
        raise ContractError(f"seed table is missing target columns: {missing}")
    if "seed" not in seed_frame.columns or "design_id" not in seed_frame.columns:
        raise ContractError("seed table needs 'design_id' and 'seed' columns")

    frame = seed_frame.sort_values(["design_id", "seed"]).reset_index(drop=True)
    design_ids = sorted(frame["design_id"].astype(str).unique())
    seed_blocks = sorted(int(s) for s in frame["seed"].unique())

    def centred(selected_designs: Sequence[str], selected_seeds: Sequence[int]) -> np.ndarray:
        rows: list[np.ndarray] = []
        for design_id in sorted(selected_designs):
            block = frame[frame["design_id"].astype(str) == design_id]
            block = block[block["seed"].astype(int).isin(list(selected_seeds))]
            if len(block) < 2:
                continue
            values = block[columns].to_numpy(dtype=float)
            rows.append(values - values.mean(axis=0, keepdims=True))
        if not rows:
            return np.empty((0, len(columns)), dtype=float)
        return np.vstack(rows)

    def build(reason: str, status: str) -> CorrelatedOutputDiagnostic:
        return CorrelatedOutputDiagnostic(
            status=status, reason=reason, n_candidates=len(design_ids),
            n_seed_blocks=len(seed_blocks), effective_rank=None,
            condition_number=None, leading_directions=(), shrinkage=float(shrinkage),
            rank_spread_bootstrap=None, rank_spread_leave_one_out=None,
            min_direction_cosine=None, rank_correlation_with_i_max=None,
        )

    # The effective stochastic sample size is the number of seed blocks. Two
    # blocks cannot support a covariance over 13 targets, and pretending
    # otherwise by counting candidate-by-seed rows is exactly the pseudo-
    # replication the specification forbids.
    if len(seed_blocks) < 3:
        return build(
            f"only {len(seed_blocks)} independent seed block(s); the effective stochastic "
            "sample size is the number of seed blocks, not candidates times seeds",
            DIAGNOSTIC_INSUFFICIENT,
        )
    if len(design_ids) < 2:
        return build(
            f"only {len(design_ids)} candidate(s); leave-one-candidate-out stability cannot "
            "be assessed",
            DIAGNOSTIC_INSUFFICIENT,
        )

    full = centred(design_ids, seed_blocks)
    if full.shape[0] < 2:
        return build(
            "fewer than two centred replicate rows survived; no covariance can be estimated",
            DIAGNOSTIC_INSUFFICIENT,
        )
    covariance = _shrunk_covariance(full, shrinkage)
    rank, condition, vectors = _effective_rank(covariance)
    leading = vectors[:, : int(n_directions)]

    # Seed-block bootstrap: resample whole seed blocks with replacement, never
    # individual candidate-by-seed rows.
    rng = np.random.default_rng(int(bootstrap_seed))
    boot_ranks: list[float] = []
    boot_cosines: list[float] = []
    for _ in range(max(0, int(n_bootstrap))):
        drawn = [int(seed_blocks[i]) for i in rng.integers(0, len(seed_blocks), len(seed_blocks))]
        resampled = centred(design_ids, sorted(set(drawn)))
        if resampled.shape[0] < 2:
            continue
        boot_rank, _, boot_vectors = _effective_rank(
            _shrunk_covariance(resampled, shrinkage)
        )
        boot_ranks.append(boot_rank)
        boot_cosines.append(_subspace_alignment(leading, boot_vectors[:, : int(n_directions)]))

    loo_ranks: list[float] = []
    loo_cosines: list[float] = []
    for dropped in design_ids:
        kept = [d for d in design_ids if d != dropped]
        resampled = centred(kept, seed_blocks)
        if resampled.shape[0] < 2:
            continue
        loo_rank, _, loo_vectors = _effective_rank(
            _shrunk_covariance(resampled, shrinkage)
        )
        loo_ranks.append(loo_rank)
        loo_cosines.append(_subspace_alignment(leading, loo_vectors[:, : int(n_directions)]))

    boot_spread = (
        float(max(boot_ranks) - min(boot_ranks)) if len(boot_ranks) >= 2 else None
    )
    loo_spread = float(max(loo_ranks) - min(loo_ranks)) if len(loo_ranks) >= 2 else None
    cosines = boot_cosines + loo_cosines
    min_cosine = float(min(cosines)) if cosines else None

    correlation = _rank_correlation_with_i_max(frame, scores, design_ids, columns, covariance)

    reasons: list[str] = []
    if boot_spread is None or loo_spread is None:
        reasons.append("the stability refits did not produce enough estimates to compare")
    else:
        if boot_spread > float(rank_tolerance):
            reasons.append(
                f"effective rank moved by {boot_spread:.3f} across seed-block bootstrap "
                f"resamples, beyond the declared tolerance {float(rank_tolerance)}"
            )
        if loo_spread > float(rank_tolerance):
            reasons.append(
                f"effective rank moved by {loo_spread:.3f} across leave-one-candidate-out "
                f"refits, beyond the declared tolerance {float(rank_tolerance)}"
            )
    if min_cosine is None or min_cosine < float(direction_cosine):
        reasons.append(
            f"leading correlated directions aligned to only {min_cosine} across refits, "
            f"below the declared {float(direction_cosine)}"
        )

    status = DIAGNOSTIC_AVAILABLE if not reasons else DIAGNOSTIC_UNSTABLE
    reason = (
        "seed-block bootstrap and leave-one-candidate-out refits agree within the declared "
        "tolerances"
        if not reasons else
        "; ".join(reasons)
        + ". The diagnostic is marked unavailable rather than repaired by dropping targets "
        "or selecting a more favourable covariance estimator."
    )
    return CorrelatedOutputDiagnostic(
        status=status,
        reason=reason,
        n_candidates=len(design_ids),
        n_seed_blocks=len(seed_blocks),
        effective_rank=rank,
        condition_number=condition,
        leading_directions=tuple(
            tuple(float(v) for v in leading[:, i]) for i in range(leading.shape[1])
        ),
        shrinkage=float(shrinkage),
        rank_spread_bootstrap=boot_spread,
        rank_spread_leave_one_out=loo_spread,
        min_direction_cosine=min_cosine,
        rank_correlation_with_i_max=correlation,
    )


def _subspace_alignment(left: np.ndarray, right: np.ndarray) -> float:
    """Worst-case |cos| between the two leading subspaces' matched directions.

    Matched greedily by largest |cos|, so a reordering of eigenvectors with
    nearly equal eigenvalues is not mistaken for instability.
    """
    if left.size == 0 or right.size == 0:
        return 0.0
    gram = np.abs(np.asarray(left, dtype=float).T @ np.asarray(right, dtype=float))
    used: set[int] = set()
    worst = 1.0
    for row in range(gram.shape[0]):
        best, best_index = -1.0, None
        for column in range(gram.shape[1]):
            if column in used:
                continue
            if gram[row, column] > best:
                best, best_index = float(gram[row, column]), column
        if best_index is None:
            break
        used.add(best_index)
        worst = min(worst, best)
    return float(worst)


def _rank_correlation_with_i_max(
    frame: pd.DataFrame,
    scores: pd.DataFrame | None,
    design_ids: Sequence[str],
    columns: Sequence[str],
    covariance: np.ndarray,
) -> float | None:
    """Spearman agreement between a correlated score and pointwise ``I_max``.

    Reported so that a material contradiction is visible and can be
    investigated (spec sec. 7).  It is a comparison, not an override: the
    correlated score never enters any pass flag.
    """
    if scores is None or scores.empty or "i_max" not in scores.columns:
        return None
    try:
        inverse = np.linalg.pinv(covariance)
    except np.linalg.LinAlgError:
        return None
    pointwise: list[float] = []
    correlated: list[float] = []
    lookup = {str(row["design_id"]): float(row["i_max"]) for _, row in scores.iterrows()}
    for design_id in sorted(design_ids):
        if design_id not in lookup:
            continue
        block = frame[frame["design_id"].astype(str) == design_id]
        mean = block[list(columns)].to_numpy(dtype=float).mean(axis=0)
        pointwise.append(lookup[design_id])
        correlated.append(float(mean @ inverse @ mean))
    if len(pointwise) < 3:
        return None
    left = pd.Series(pointwise).rank().to_numpy(dtype=float)
    right = pd.Series(correlated).rank().to_numpy(dtype=float)
    if left.std() == 0.0 or right.std() == 0.0:
        return None
    return float(np.corrcoef(left, right)[0, 1])


__all__ = [name for name in dir() if not name.startswith("_")]
