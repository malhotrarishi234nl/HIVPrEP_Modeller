#!/usr/bin/env python3
"""Sampled-path connectivity for the robust-NROY freeze stage (spec sec. 4).

The central discipline of this module is what it refuses to claim.

* Euclidean distance in the normalised metric is used **only** to propose
  candidate edges and to order them.  It never establishes a region.
* An edge is ``sampled_path_supported`` only when both endpoints *and every*
  declared bridge probe confirm.  That is discrete evidence at three
  interior points, not a proof of continuous connectivity.
* A failed straight bridge is ``unresolved``, never proof of disconnection: a
  correlated region may curve between two plausible points, and a straight
  line through parameter space is only one proposal about how to get there.
* A "component" is therefore a set of points joined by sampled paths under the
  declared design.  It is not a topological component of the NROY set, and the
  module never names it one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

import nroy_contract as nc
from coupled_calibration_search import ContractError

#: Declared minimal-edge algorithms.  The plan names one; nothing is inferred.
EDGE_ALGORITHMS = ("kruskal_minimum_spanning_tree",)

#: Declared candidate-edge rules.  Complete pairwise proposal is deliberate:
#: a sparse nearest-neighbour prefilter can omit the only edge between two
#: groups before the auditable minimum-spanning-tree selection even begins.
EDGE_PROPOSAL_RULES = ("complete_pairwise_distances",)

#: Declared bridge pass rules.  Only the specification's own rule exists: spec
#: sec. 4 requires "the exact rule that all 25/50/75% probes must pass".  The
#: registry exists so the signed value is *checked* rather than assumed, and so
#: a plan that declares some other rule is refused rather than silently given
#: this behaviour.
BRIDGE_PASS_RULES = ("all_declared_probes_must_confirm",)

#: Declared representative-selection rules.  Geometry only, by construction.
#: Only rules this module actually implements are listed: an entry here is a
#: promise that the signed value is honoured, so the registry is deliberately
#: not a wish list.
REPRESENTATIVE_RULES = ("maximin_geometry",)

#: Declared deterministic tie-breaking rules.
TIE_BREAKING_RULES = ("candidate_id_ascending",)


@dataclass(frozen=True)
class CandidateEdge:
    """A proposed edge.  Proposal only: distance never establishes support."""

    edge_id: str
    left: str
    right: str
    distance: float
    rank: int

    def as_row(self) -> dict:
        return {
            "edge_id": self.edge_id,
            "left_candidate_id": self.left,
            "right_candidate_id": self.right,
            "distance_normalised": float(self.distance),
            "proposal_rank": int(self.rank),
        }


@dataclass(frozen=True)
class EdgeVerdict:
    """The evaluated status of one proposed edge."""

    edge_id: str
    left: str
    right: str
    distance: float
    label: str
    n_probes_declared: int
    n_probes_confirmed: int
    n_probes_failed: int
    n_probes_not_run: int
    evidence: str
    reason: str

    def __post_init__(self) -> None:
        if self.label not in nc.EDGE_LABELS:
            raise ContractError(f"unknown edge label {self.label!r}")
        if self.evidence not in nc.EVIDENCE_STATES:
            raise ContractError(f"unknown evidence state {self.evidence!r}")

    @property
    def supported(self) -> bool:
        return self.label == nc.EDGE_SAMPLED_PATH_SUPPORTED

    def as_row(self) -> dict:
        return {
            "edge_id": self.edge_id,
            "left_candidate_id": self.left,
            "right_candidate_id": self.right,
            "distance_normalised": float(self.distance),
            "edge_label": self.label,
            "evidence_state": self.evidence,
            "n_bridge_probes_declared": self.n_probes_declared,
            "n_bridge_probes_confirmed": self.n_probes_confirmed,
            "n_bridge_probes_failed": self.n_probes_failed,
            "n_bridge_probes_not_run": self.n_probes_not_run,
            "reason": self.reason,
        }


def _edge_id(left: str, right: str) -> str:
    a, b = sorted((str(left), str(right)))
    return f"{a}--{b}"


def candidate_edges(
    points: Mapping[str, Sequence[float]],
    *,
    proposal_rule: str,
    tie_breaking: str = "candidate_id_ascending",
) -> list[CandidateEdge]:
    """Build every pairwise edge in normalised space.

    Deterministic by construction: distances are computed in the frozen
    normalised metric and ties are broken by the declared rule (candidate id),
    so the proposal set never depends on dictionary or file ordering.

    No nearest-neighbour prefilter is applied.  Kruskal therefore sees the
    complete graph and returns exactly ``n-1`` edges for ``n>=2`` finite
    points.  Distance only proposes which straight paths to test; it never
    establishes NROY connectivity.
    """
    if proposal_rule not in EDGE_PROPOSAL_RULES:
        raise ContractError(
            f"unknown edge-proposal rule {proposal_rule!r}; the plan must declare one of "
            f"{EDGE_PROPOSAL_RULES}"
        )
    if tie_breaking not in TIE_BREAKING_RULES:
        raise ContractError(
            f"unknown tie-breaking rule {tie_breaking!r}; the plan must declare one of "
            f"{TIE_BREAKING_RULES}"
        )
    ids = sorted(points)
    if len(ids) < 2:
        return []
    try:
        matrix = np.asarray([np.asarray(points[i], dtype=float) for i in ids], dtype=float)
    except (TypeError, ValueError) as exc:
        raise ContractError("graph points must be a rectangular finite matrix") from exc
    if matrix.ndim != 2 or not np.all(np.isfinite(matrix)):
        raise ContractError("graph points must be a rectangular finite matrix")

    distances = {
        (ids[left], ids[right]): float(np.linalg.norm(matrix[left] - matrix[right]))
        for left in range(len(ids))
        for right in range(left + 1, len(ids))
    }
    ordered = sorted(distances, key=lambda pair: (distances[pair], pair[0], pair[1]))
    return [
        CandidateEdge(
            edge_id=_edge_id(left, right),
            left=left,
            right=right,
            distance=distances[(left, right)],
            rank=rank,
        )
        for rank, (left, right) in enumerate(ordered)
    ]


class _DisjointSet:
    def __init__(self, items: Sequence[str]):
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        # Deterministic: the lexicographically smaller root wins.
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra
        return True


def minimal_edge_set(
    edges: Sequence[CandidateEdge],
    *,
    algorithm: str,
    node_ids: Sequence[str],
) -> list[CandidateEdge]:
    """Choose the minimal auditable edge set needed to establish components.

    ``kruskal_minimum_spanning_tree`` walks the proposed edges in ascending
    distance (ties by candidate id) and keeps an edge only when it would join
    two currently-separate groups.  That is the smallest set of bridges whose
    evaluation can establish the component structure, which keeps the
    simulation budget on edges that can actually change the answer.

    The choice is a *proposal* about which bridges to test.  It does not make
    the resulting components topological.
    """
    if algorithm not in EDGE_ALGORITHMS:
        raise ContractError(
            f"unknown minimal-edge algorithm {algorithm!r}; the plan must declare one of "
            f"{EDGE_ALGORITHMS}"
        )
    forest = _DisjointSet(sorted(node_ids))
    kept: list[CandidateEdge] = []
    for edge in sorted(edges, key=lambda e: (e.distance, e.left, e.right)):
        if forest.union(edge.left, edge.right):
            kept.append(edge)
    return kept


def classify_edge(
    edge: CandidateEdge,
    *,
    endpoint_confirmed: Mapping[str, bool],
    probe_evidence: Sequence[str],
    n_declared: int,
    pass_rule: str,
) -> EdgeVerdict:
    """Label one proposed edge from its bridge-probe evidence (spec sec. 4).

    ``probe_evidence`` holds one state per evaluated bridge probe, drawn from
    the module-level evidence vocabulary.  ``pass_rule`` is the plan's signed
    ``graph.bridge_pass_rule`` and is **checked, not assumed**: the behaviour
    below implements ``all_declared_probes_must_confirm``, so any other
    declared rule is refused rather than silently given this one.

    Under that rule: *every* declared probe must confirm, and both endpoints
    must be confirmed points.  Anything else is ``unresolved``.
    """
    if pass_rule not in BRIDGE_PASS_RULES:
        raise ContractError(
            f"unknown bridge pass rule {pass_rule!r}; this implementation provides "
            f"{BRIDGE_PASS_RULES} and will not silently substitute one for another"
        )
    left_ok = bool(endpoint_confirmed.get(edge.left, False))
    right_ok = bool(endpoint_confirmed.get(edge.right, False))
    states = [str(s) for s in probe_evidence]
    for state in states:
        if state not in nc.EVIDENCE_STATES:
            raise ContractError(f"unknown probe evidence state {state!r}")
    n_confirmed = sum(1 for s in states if s == nc.EVIDENCE_CONFIRMED)
    n_failed = sum(1 for s in states if s == nc.EVIDENCE_EXECUTION_FAILURE)
    n_not_run = sum(1 for s in states if s == nc.EVIDENCE_NOT_RUN) + max(
        0, int(n_declared) - len(states)
    )

    def verdict(label: str, evidence: str, reason: str) -> EdgeVerdict:
        return EdgeVerdict(
            edge_id=edge.edge_id, left=edge.left, right=edge.right, distance=edge.distance,
            label=label, evidence=evidence, reason=reason,
            n_probes_declared=int(n_declared), n_probes_confirmed=n_confirmed,
            n_probes_failed=n_failed, n_probes_not_run=n_not_run,
        )

    if not (left_ok and right_ok):
        unconfirmed = [n for n, ok in ((edge.left, left_ok), (edge.right, right_ok)) if not ok]
        return verdict(
            nc.EDGE_UNRESOLVED, nc.EVIDENCE_UNRESOLVED,
            f"endpoint(s) not confirmed: {unconfirmed}",
        )
    if n_failed:
        return verdict(
            nc.EDGE_UNRESOLVED, nc.EVIDENCE_EXECUTION_FAILURE,
            f"{n_failed} bridge probe(s) failed to execute; execution failure is not evidence "
            "of disconnection",
        )
    if n_not_run:
        return verdict(
            nc.EDGE_UNRESOLVED, nc.EVIDENCE_NOT_RUN,
            f"{n_not_run} declared bridge probe(s) were not run",
        )
    if n_confirmed == int(n_declared) and int(n_declared) > 0:
        return verdict(
            nc.EDGE_SAMPLED_PATH_SUPPORTED, nc.EVIDENCE_CONFIRMED,
            f"both endpoints and all {int(n_declared)} declared bridge probes confirmed; "
            "discrete evidence along the proposed path, not continuous connectivity",
        )
    return verdict(
        nc.EDGE_UNRESOLVED, nc.EVIDENCE_UNRESOLVED,
        f"only {n_confirmed} of {int(n_declared)} bridge probes confirmed; a failed straight "
        "bridge is unresolved, not proof of disconnection, because a correlated region may curve",
    )


@dataclass(frozen=True)
class Component:
    """A set of points joined by sampled paths under the declared design."""

    component_id: str
    members: tuple[str, ...]
    supporting_edges: tuple[str, ...]
    unresolved_edges: tuple[str, ...]

    def as_row(self) -> dict:
        return {
            "component_id": self.component_id,
            "n_members": len(self.members),
            "members": "|".join(self.members),
            "n_supporting_edges": len(self.supporting_edges),
            "supporting_edges": "|".join(self.supporting_edges),
            "n_unresolved_boundary_edges": len(self.unresolved_edges),
            "unresolved_boundary_edges": "|".join(self.unresolved_edges),
        }


def components(
    node_ids: Sequence[str],
    verdicts: Sequence[EdgeVerdict],
) -> list[Component]:
    """Group points by sampled-path-supported edges only.

    Unresolved edges are recorded against the components they would have
    joined, so a reader can see exactly which grouping decisions are still
    open rather than being shown a clean partition that hides them.  A
    singleton is a legitimate component: one confirmed point with local
    support and no supported bridge is still eligible on its own.
    """
    ids = sorted(str(n) for n in node_ids)
    forest = _DisjointSet(ids)
    supported: dict[str, list[str]] = {}
    for verdict in sorted(verdicts, key=lambda v: v.edge_id):
        if verdict.supported:
            if verdict.left not in forest.parent or verdict.right not in forest.parent:
                raise ContractError(
                    f"edge {verdict.edge_id} references a node outside the declared set"
                )
            forest.union(verdict.left, verdict.right)
    groups: dict[str, list[str]] = {}
    for identifier in ids:
        groups.setdefault(forest.find(identifier), []).append(identifier)

    for verdict in sorted(verdicts, key=lambda v: v.edge_id):
        if verdict.supported:
            supported.setdefault(forest.find(verdict.left), []).append(verdict.edge_id)

    unresolved: dict[str, list[str]] = {}
    for verdict in sorted(verdicts, key=lambda v: v.edge_id):
        if not verdict.supported:
            for endpoint in (verdict.left, verdict.right):
                if endpoint in forest.parent:
                    unresolved.setdefault(forest.find(endpoint), []).append(verdict.edge_id)

    out: list[Component] = []
    for index, root in enumerate(sorted(groups), start=1):
        out.append(Component(
            component_id=f"R{index:03d}",
            members=tuple(sorted(groups[root])),
            supporting_edges=tuple(sorted(set(supported.get(root, ())))),
            unresolved_edges=tuple(sorted(set(unresolved.get(root, ())))),
        ))
    return out


def unresolved_changes_representation(
    components_now: Sequence[Component],
    verdicts: Sequence[EdgeVerdict],
) -> list[str]:
    """Which unresolved edges would merge two components if they were resolved.

    These are the only unresolved edges that can change which groups receive
    representatives, and therefore the only ones that justify spending a
    bounded archive-informed path search (spec sec. 4).  Reporting them keeps
    the escalation decision evidence-driven rather than discretionary.
    """
    owner: dict[str, str] = {}
    for component in components_now:
        for member in component.members:
            owner[member] = component.component_id
    out: list[str] = []
    for verdict in sorted(verdicts, key=lambda v: v.edge_id):
        if verdict.supported:
            continue
        left, right = owner.get(verdict.left), owner.get(verdict.right)
        if left and right and left != right:
            out.append(verdict.edge_id)
    return out


def select_representatives(
    components_now: Sequence[Component],
    geometry: Mapping[str, Sequence[float]],
    *,
    n_per_component: int,
    rule: str,
    eligible: Mapping[str, bool] | None = None,
) -> pd.DataFrame:
    """Choose representatives from each component on geometry alone.

    **Policy outcomes are not an argument to this function and cannot be.**
    Selection sees normalised parameter/baseline geometry and nothing else
    (spec sec. 4), which is what stops representatives being chosen for the
    forecasts they happen to produce.

    ``maximin_geometry`` picks the member furthest from the component centroid
    first, then repeatedly the member furthest from those already chosen.
    Ties break on candidate id, so the selection is reproducible.

    Only members flagged eligible -- that is, with demonstrated local support
    -- may be selected.  A component whose members are all ineligible yields
    no representative and is reported as such rather than silently dropped.
    """
    if rule not in REPRESENTATIVE_RULES:
        raise ContractError(
            f"unknown representative-selection rule {rule!r}; it must be declared and signed "
            f"as one of {REPRESENTATIVE_RULES}"
        )
    if int(n_per_component) < 1:
        raise ContractError(
            f"representative_selection.n_per_component must be >= 1, got {n_per_component!r}"
        )
    rows: list[dict] = []
    for component in components_now:
        members = [
            m for m in component.members
            if (eligible is None or bool(eligible.get(m, False)))
        ]
        skipped = [m for m in component.members if m not in members]
        if not members:
            rows.append({
                "component_id": component.component_id,
                "candidate_id": None,
                "selection_rank": None,
                "n_eligible_members": 0,
                "selection_rule": rule,
                "note": (
                    "no member has demonstrated local support; this component receives no "
                    "representative and is reported, not silently dropped"
                ),
            })
            continue
        matrix = np.asarray([np.asarray(geometry[m], dtype=float) for m in members], dtype=float)
        centroid = matrix.mean(axis=0)
        first = min(
            range(len(members)),
            key=lambda i: (-float(np.linalg.norm(matrix[i] - centroid)), members[i]),
        )
        chosen = [first]
        while len(chosen) < min(int(n_per_component), len(members)):
            best = None
            for index in range(len(members)):
                if index in chosen:
                    continue
                gap = min(float(np.linalg.norm(matrix[index] - matrix[c])) for c in chosen)
                key = (-gap, members[index])
                if best is None or key < best[0]:
                    best = (key, index)
            chosen.append(best[1])
        for rank, index in enumerate(chosen, start=1):
            rows.append({
                "component_id": component.component_id,
                "candidate_id": members[index],
                "selection_rank": rank,
                "n_eligible_members": len(members),
                "selection_rule": rule,
                "note": (
                    f"{len(skipped)} member(s) excluded for lacking demonstrated local support"
                    if skipped else ""
                ),
            })
    return pd.DataFrame(rows)


def graph_tables(
    edges: Sequence[CandidateEdge],
    minimal: Sequence[CandidateEdge],
    verdicts: Sequence[EdgeVerdict],
    components_now: Sequence[Component],
) -> dict[str, pd.DataFrame]:
    """Tidy machine-readable graph tables."""
    minimal_ids = {e.edge_id for e in minimal}
    edge_rows = []
    for edge in sorted(edges, key=lambda e: e.rank):
        row = edge.as_row()
        row["in_minimal_edge_set"] = edge.edge_id in minimal_ids
        edge_rows.append(row)
    return {
        "candidate_edges": pd.DataFrame(edge_rows),
        "edge_verdicts": pd.DataFrame([v.as_row() for v in
                                       sorted(verdicts, key=lambda v: v.edge_id)]),
        "components": pd.DataFrame([c.as_row() for c in components_now]),
    }


__all__ = [name for name in dir() if not name.startswith("_")]
