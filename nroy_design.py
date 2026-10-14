#!/usr/bin/env python3
"""Deterministic probe design for the robust-NROY freeze stage (spec sec. 3-4).

Every proposal this module produces is frozen *before* simulation and carries
its own provenance: kernel, version, scale, direction class, ordered parent
ids, pre-/post-constraint vector hash, RNG state and, when rejected, the
reasons.  Nothing here evaluates a candidate, builds an engine or writes a file.

Four hard rules, from the specification:

* **No clipping.**  An invalid proposal is redrawn, never repaired, snapped or
  projected back into the feasible set.
* **No silent fallback.**  A kernel that exhausts its attempts fails
  preparation.  It may not borrow another kernel's quota, nor pad its own with
  duplicates or global draws.
* **Distance proposes only.**  Euclidean distance in the normalised metric
  orders candidate edges and measures probe scale.  It never establishes
  support or connectivity.
* **Complete joint vectors only.**  Probes move a full 33/34-coordinate vector.
  No vector is ever assembled from independent marginals.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Callable, Mapping, Sequence

import numpy as np

import coupled_calibration_search as ccs
import nroy_contract as nc
from coupled_calibration_search import ContractError

#: Bumped whenever the geometry of any kernel changes.  Recorded per proposal,
#: so a design can never be silently mixed across kernel versions.
KERNEL_VERSION = "1.0.0"

#: Declared direction generators.  The plan must name one; the code never
#: infers it from a default (spec sec. 3.1).
DIRECTION_GENERATORS = ("gaussian_unit_sphere", "uniform_coordinate_box")

# Direction classes, used for the "independent direction classes" requirement
# that stops ridge support resting on duplicate or nearly collinear moves.
DIRCLASS_AMBIENT = "ambient_isotropic"
DIRCLASS_ARCHIVE_DIFF = "archive_difference"
DIRCLASS_CONFIRMED_DIFF = "confirmed_difference"
DIRCLASS_CROSS_BLOCK = "cross_block_compensating"
DIRCLASS_BRIDGE = "bridge_interpolation"
DIRCLASS_P_BOUNDARY = "p_boundary_one_sided"

#: Two directions counted as the same class if |cos| exceeds this.  A declared
#: computational threshold for *independence of direction classes*; it is not a
#: scientific criterion and never affects acceptance.
COLLINEAR_COSINE = 0.99


@dataclass(frozen=True)
class Proposal:
    """One frozen probe, with complete provenance (spec sec. 3 last paragraph)."""

    design_id: str
    stage: str
    model: str
    kernel: str
    kernel_version: str
    scale: float
    direction_class: str
    parent_ids: tuple[str, ...]
    params: dict[str, float]
    vector_hash: str
    pre_constraint_vector_hash: str
    distance_to_parent: float
    rng_state_hash: str
    attempt_index: int
    accepted: bool = True
    rejection_reasons: tuple[str, ...] = ()
    notes: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kernel not in nc.KERNEL_FAMILIES:
            raise ContractError(f"unknown kernel {self.kernel!r}")
        if not self.kernel:
            raise ContractError("kernel_id may never be null")
        if not self.parent_ids:
            raise ContractError(
                f"proposal {self.design_id} has no parent; every probe must trace to its "
                "exact parent vector(s)"
            )
        if self.accepted and self.rejection_reasons:
            raise ContractError(
                f"proposal {self.design_id} is accepted but carries rejection reasons"
            )

    def as_row(self) -> dict:
        return {
            "design_id": self.design_id,
            "stage": self.stage,
            "model": self.model,
            "kernel_id": self.kernel,
            "kernel_version": self.kernel_version,
            "scale": float(self.scale),
            "direction_class": self.direction_class,
            "parent_ids": "|".join(self.parent_ids),
            "n_parents": len(self.parent_ids),
            "vector_hash": self.vector_hash,
            "pre_constraint_vector_hash": self.pre_constraint_vector_hash,
            "distance_to_parent": float(self.distance_to_parent),
            "rng_state_hash": self.rng_state_hash,
            "attempt_index": int(self.attempt_index),
            "accepted": bool(self.accepted),
            "rejection_reasons": "|".join(self.rejection_reasons),
        }


@dataclass
class KernelOutcome:
    """What one kernel produced for one anchor, including what it threw away."""

    kernel: str
    anchor_id: str
    quota: int
    accepted: list[Proposal] = field(default_factory=list)
    rejected: list[Proposal] = field(default_factory=list)
    attempts: int = 0
    exhausted: bool = False
    feasible_direction_coverage: float | None = None
    rejection_counts: dict[str, int] = field(default_factory=dict)

    def as_row(self) -> dict:
        return {
            "kernel_id": self.kernel,
            "anchor_id": self.anchor_id,
            "quota": int(self.quota),
            "n_proposed": int(self.attempts),
            "n_accepted": len(self.accepted),
            "n_rejected": len(self.rejected),
            "attempts": int(self.attempts),
            "redraw_exhausted": bool(self.exhausted),
            "feasible_direction_coverage": self.feasible_direction_coverage,
            "rejection_counts": dict(sorted(self.rejection_counts.items())),
        }


class KernelExhaustedError(ContractError):
    """A kernel could not fill its declared quota.

    This fails *preparation*.  The specification is explicit that a kernel may
    not silently fall back to another kernel or fill its quota with duplicate
    or global draws, so there is deliberately no recovery path here.
    """


# ==========================================================================
# RNG discipline
# ==========================================================================

def design_rng(seed: int, *stream: object) -> np.random.Generator:
    """A deterministic sub-stream of the frozen design seed.

    The stream is keyed by the hash of its labels, so the generator for
    ``(anchor, kernel, scale)`` does not depend on how many other anchors or
    kernels were processed first.  Shard count, worker count and iteration
    order therefore cannot change any proposal.
    """
    label = "|".join(str(part) for part in stream)
    digest = hashlib.sha256(f"{int(seed)}|{label}".encode()).digest()
    # SeedSequence accepts arbitrary-width entropy; take 128 bits of the digest.
    entropy = int.from_bytes(digest[:16], "big")
    return np.random.default_rng(np.random.SeedSequence([int(seed), entropy]))


def rng_state_hash(rng: np.random.Generator) -> str:
    """A stable fingerprint of generator state, recorded with every proposal."""
    state = rng.bit_generator.state
    return ccs.sha256_json(nc.json_safe(state))


# ==========================================================================
# Directions
# ==========================================================================

def _unit_sphere(rng: np.random.Generator, n: int, count: int) -> np.ndarray:
    """Rows uniform on the unit sphere in ``n`` dimensions."""
    draw = rng.standard_normal((int(count), int(n)))
    norms = np.linalg.norm(draw, axis=1, keepdims=True)
    # A zero row has probability zero but is not impossible; redraw rather than
    # divide by zero, because a silently NaN direction would be a fake probe.
    bad = np.flatnonzero(norms[:, 0] == 0.0)
    while bad.size:
        draw[bad] = rng.standard_normal((bad.size, int(n)))
        norms = np.linalg.norm(draw, axis=1, keepdims=True)
        bad = np.flatnonzero(norms[:, 0] == 0.0)
    return draw / norms


def _coordinate_box(rng: np.random.Generator, n: int, count: int) -> np.ndarray:
    """Rows uniform in the cube ``[-1, 1]^n`` (coordinate-wise bound)."""
    return rng.uniform(-1.0, 1.0, size=(int(count), int(n)))


def generate_directions(
    rng: np.random.Generator,
    n: int,
    count: int,
    *,
    generator: str,
) -> np.ndarray:
    if generator not in DIRECTION_GENERATORS:
        raise ContractError(
            f"unknown direction generator {generator!r}; the plan must declare one of "
            f"{DIRECTION_GENERATORS} and the code never infers it"
        )
    if generator == "gaussian_unit_sphere":
        return _unit_sphere(rng, n, count)
    return _coordinate_box(rng, n, count)


def maximin_subset(directions: np.ndarray, k: int) -> np.ndarray:
    """Greedy maximin selection of ``k`` rows: spread-out probe directions.

    Deterministic given the input order: the first row is the one furthest
    from the centroid (ties by index), and each subsequent pick maximises the
    minimum distance to those already chosen (ties by index).
    """
    pool = np.atleast_2d(np.asarray(directions, dtype=float))
    want = int(k)
    if want <= 0:
        return np.empty((0, pool.shape[1]))
    if pool.shape[0] <= want:
        return pool
    centroid = pool.mean(axis=0)
    first = int(np.argmax(np.linalg.norm(pool - centroid, axis=1)))
    chosen = [first]
    min_dist = np.linalg.norm(pool - pool[first], axis=1)
    while len(chosen) < want:
        min_dist[chosen] = -np.inf
        nxt = int(np.argmax(min_dist))
        chosen.append(nxt)
        min_dist = np.minimum(min_dist, np.linalg.norm(pool - pool[nxt], axis=1))
    return pool[np.array(chosen, dtype=int)]


def direction_classes(vectors: Sequence[np.ndarray], *, cosine: float = COLLINEAR_COSINE) -> int:
    """How many mutually non-collinear directions a set contains.

    Used for the "independent direction classes" requirement: two duplicate or
    nearly collinear moves are one class, not two, so ridge support cannot rest
    on the same direction sampled twice.
    """
    reps: list[np.ndarray] = []
    for raw in vectors:
        vector = np.asarray(raw, dtype=float)
        norm = np.linalg.norm(vector)
        if norm == 0.0:
            continue
        unit = vector / norm
        if not any(abs(float(unit @ other)) > float(cosine) for other in reps):
            reps.append(unit)
    return len(reps)


# ==========================================================================
# Validity, without clipping
# ==========================================================================

@dataclass(frozen=True)
class Validator:
    """Bundles every reason a proposal may be refused.

    Deliberately *reports* reasons rather than fixing anything.  The caller's
    only legitimate response to an invalid proposal is to redraw it.
    """

    space: ccs.ParameterSpace
    model: nc.ModelSpace
    injection_check: Callable[[dict[str, float]], None] | None = None

    def reasons(self, params: Mapping[str, float]) -> list[str]:
        """Every reason to refuse, which is the declared bounds and constraints.

        There is deliberately no ``p``-proximity rule here.  An earlier version
        refused probes in ``(0, identity_epsilon)`` because the engine's RNG
        streams change at any positive ``p``; both specifications now prohibit
        that band, since stream re-keying constrains how a probe may be
        *compared*, not whether its parameter value is valid.  See
        :func:`nroy_contract.assert_comparison_mode`.
        """
        vector = np.array([float(params[name]) for name in self.space.names], dtype=float)
        return list(
            ccs.invalid_reasons(self.space, vector, injection_check=self.injection_check)
        )


# ==========================================================================
# Anchors
# ==========================================================================

@dataclass(frozen=True)
class Anchor:
    """A confirmed point whose neighbourhood is being probed."""

    anchor_id: str
    params: dict[str, float]
    vector_hash: str
    model: str

    @classmethod
    def build(cls, anchor_id: str, params: Mapping[str, float], model: nc.ModelSpace) -> "Anchor":
        complete = {name: float(params[name]) for name in model.names}
        return cls(
            anchor_id=str(anchor_id),
            params=complete,
            vector_hash=model.vector_hash(complete),
            model=model.model,
        )

    def u(self, model: nc.ModelSpace) -> np.ndarray:
        return model.normalise(self.params)


def _design_id(stage: str, model: str, kernel: str, anchor_id: str, index: int) -> str:
    """Globally unique, deterministic work-unit identity."""
    return f"{stage}/{model}/{kernel}/{anchor_id}/{index:04d}"


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)))


def _make_proposal(
    *,
    stage: str,
    model: nc.ModelSpace,
    kernel: str,
    anchor: Anchor,
    index: int,
    u_new: np.ndarray,
    scale: float,
    direction_class: str,
    parent_ids: Sequence[str],
    validator: Validator,
    rng: np.random.Generator,
    attempt_index: int,
    extra_reasons: Sequence[str] = (),
) -> Proposal:
    """Build one proposal and record its validity.  Never modifies ``u_new``."""
    x_new = model.denormalise(u_new)
    params = {name: float(value) for name, value in zip(model.names, x_new)}
    # The pre-constraint hash is taken from the raw proposal, before any
    # validity verdict, so an audit can see exactly what the kernel proposed.
    pre_hash = model.vector_hash(params)
    reasons = list(extra_reasons) + validator.reasons(params)
    anchor_u = anchor.u(model)
    return Proposal(
        design_id=_design_id(stage, model.model, kernel, anchor.anchor_id, index),
        stage=stage,
        model=model.model,
        kernel=kernel,
        kernel_version=KERNEL_VERSION,
        scale=float(scale),
        direction_class=direction_class,
        parent_ids=tuple(str(p) for p in parent_ids),
        params=params,
        vector_hash=pre_hash,
        pre_constraint_vector_hash=pre_hash,
        distance_to_parent=_distance(u_new, anchor_u),
        rng_state_hash=rng_state_hash(rng),
        attempt_index=int(attempt_index),
        accepted=not reasons,
        rejection_reasons=tuple(reasons),
    )


#: Stable rejection reason codes.  The full human-readable reason (with the
#: offending value) stays on the proposal for the audit trail; the *counts* are
#: keyed by these codes so the audit table has a fixed, comparable schema
#: instead of one column per offending float.
REASON_OUT_OF_BOUNDS = "out_of_bounds"
REASON_NON_FINITE = "non_finite"
REASON_CONSTRAINT = "constraint_violation"
REASON_INJECTION = "production_injection_rejected"
REASON_DUPLICATE = "duplicate_vector_hash"
REASON_ANCHOR_SEPARATION = "below_min_anchor_neighbour_separation"
REASON_NEIGHBOUR_SEPARATION = "below_min_neighbour_neighbour_separation"
REASON_DIMENSION = "wrong_dimension"
REASON_OTHER = "other"

REASON_CODES = (
    REASON_OUT_OF_BOUNDS,
    REASON_NON_FINITE,
    REASON_CONSTRAINT,
    REASON_INJECTION,
    REASON_DUPLICATE,
    REASON_ANCHOR_SEPARATION,
    REASON_NEIGHBOUR_SEPARATION,
    REASON_DIMENSION,
    REASON_OTHER,
)


def reason_code(reason: str) -> str:
    """Map a free-text rejection reason onto a stable code."""
    text = str(reason)
    if text in REASON_CODES:
        return text
    if "below lower bound" in text or "above upper bound" in text:
        return REASON_OUT_OF_BOUNDS
    if "non-finite" in text:
        return REASON_NON_FINITE
    if "declared draw constraints" in text:
        return REASON_CONSTRAINT
    if "production injection rejected" in text:
        return REASON_INJECTION
    if "wrong dimension" in text:
        return REASON_DIMENSION
    return REASON_OTHER


def _register_rejection(outcome: KernelOutcome, proposal: Proposal) -> None:
    outcome.rejected.append(proposal)
    for reason in proposal.rejection_reasons:
        code = reason_code(reason)
        outcome.rejection_counts[code] = outcome.rejection_counts.get(code, 0) + 1


def _refused(proposal: Proposal, reason: str) -> Proposal:
    """The same proposal, recorded as refused for ``reason``.

    Provenance is carried across verbatim: a refused probe stays in the audit
    trail with its original design id, vector hash and RNG state.
    """
    return replace(proposal, accepted=False, rejection_reasons=(reason,))


def _accept(
    outcome: KernelOutcome,
    proposal: Proposal,
    seen: dict[str, Proposal],
    *,
    min_anchor_sep: float,
    min_neighbour_sep: float,
    anchor_u: np.ndarray,
    model: nc.ModelSpace,
) -> bool:
    """Accept a valid proposal if it is genuinely a *new, separated* neighbour.

    Only unique vector hashes count as neighbours, and the declared minimum
    anchor-neighbour and neighbour-neighbour separations are enforced here
    (spec sec. 3), so "two confirming neighbours" can never be one point
    counted twice or two points a hair apart.
    """
    if proposal.vector_hash in seen:
        _register_rejection(outcome, _refused(proposal, "duplicate_vector_hash"))
        return False
    u_new = model.normalise(proposal.params)
    if _distance(u_new, anchor_u) < float(min_anchor_sep):
        _register_rejection(
            outcome, _refused(proposal, "below_min_anchor_neighbour_separation")
        )
        return False
    for existing in seen.values():
        if _distance(u_new, model.normalise(existing.params)) < float(min_neighbour_sep):
            _register_rejection(
                outcome, _refused(proposal, "below_min_neighbour_neighbour_separation")
            )
            return False
    seen[proposal.vector_hash] = proposal
    outcome.accepted.append(proposal)
    return True


# ==========================================================================
# Kernel 1: ambient probes (spec sec. 3.1)
# ==========================================================================

def ambient_probes(
    anchor: Anchor,
    model: nc.ModelSpace,
    validator: Validator,
    *,
    stage: str,
    scale: float,
    quota: int,
    generator: str,
    scale_semantics: str,
    seed: int,
    min_anchor_sep: float,
    min_neighbour_sep: float,
    max_attempts: int,
    oversample: int = 16,
    index_offset: int = 0,
) -> KernelOutcome:
    """Constrained maximin joint perturbations at one declared scale.

    ``scale_semantics`` is taken from the plan, never inferred:

    * ``"radius"``: ``u_new = u + scale * d`` with ``||d|| = 1``, so every
      probe sits at Euclidean distance ``scale`` in the normalised metric.
    * ``"coordinate_bound"``: ``u_new = u + scale * v`` with
      ``v in [-1, 1]^n``, so no single coordinate moves by more than ``scale``.

    Invalid proposals are **redrawn**, never clipped.  At a bound, the fraction
    of declared directions that turned out feasible is reported as
    ``feasible_direction_coverage`` so that a one-sided neighbourhood is not
    mistaken for isolation, and ``exhausted`` records that the redraw budget
    ran out.
    """
    if scale_semantics not in ("radius", "coordinate_bound"):
        raise ContractError(f"unknown ambient scale semantics {scale_semantics!r}")
    outcome = KernelOutcome(kernel=nc.KERNEL_AMBIENT, anchor_id=anchor.anchor_id, quota=int(quota))
    anchor_u = anchor.u(model)
    seen: dict[str, Proposal] = {}
    rng = design_rng(seed, "ambient", anchor.vector_hash, scale, generator, scale_semantics)
    n = model.n
    index = int(index_offset)
    n_directions_tried = 0
    n_directions_feasible = 0

    while len(outcome.accepted) < int(quota) and outcome.attempts < int(max_attempts):
        remaining = int(quota) - len(outcome.accepted)
        pool = generate_directions(rng, n, remaining * int(oversample), generator=generator)
        picked = maximin_subset(pool, remaining)
        progressed = False
        for direction in picked:
            if len(outcome.accepted) >= int(quota):
                break
            outcome.attempts += 1
            n_directions_tried += 1
            u_new = anchor_u + float(scale) * np.asarray(direction, dtype=float)
            proposal = _make_proposal(
                stage=stage, model=model, kernel=nc.KERNEL_AMBIENT, anchor=anchor,
                index=index, u_new=u_new, scale=float(scale),
                direction_class=DIRCLASS_AMBIENT, parent_ids=(anchor.anchor_id,),
                validator=validator, rng=rng, attempt_index=outcome.attempts,
            )
            index += 1
            if not proposal.accepted:
                _register_rejection(outcome, proposal)
                continue
            n_directions_feasible += 1
            if _accept(
                outcome, proposal, seen,
                min_anchor_sep=min_anchor_sep, min_neighbour_sep=min_neighbour_sep,
                anchor_u=anchor_u, model=model,
            ):
                progressed = True
        if not progressed and outcome.attempts >= int(max_attempts):
            break

    outcome.feasible_direction_coverage = (
        float(n_directions_feasible) / float(n_directions_tried) if n_directions_tried else None
    )
    outcome.exhausted = len(outcome.accepted) < int(quota)
    return outcome


# ==========================================================================
# Kernel 2: correlation-following probes (spec sec. 3.2)
# ==========================================================================

def archive_directions(
    anchor: Anchor,
    model: nc.ModelSpace,
    *,
    archive: Sequence[Mapping[str, float]],
    archive_ids: Sequence[str],
    neighbour_count: int,
    rank_truncation: int,
) -> list[tuple[np.ndarray, str, tuple[str, ...]]]:
    """Reproducible directions derived from nearby archive vectors.

    Returns ``(direction, direction_class, parent_ids)`` triples.  The source
    pool, neighbour rule and rank truncation come from the plan and are frozen
    before any outcome is evaluated.  "Low J" ordering guides which archive
    vectors are consulted; it never establishes plausibility, and no J value
    enters the returned geometry.
    """
    if len(archive) != len(archive_ids):
        raise ContractError("archive vectors and ids differ in length")
    anchor_u = anchor.u(model)
    rows: list[tuple[float, int]] = []
    for position, params in enumerate(archive):
        missing = [name for name in model.names if name not in params]
        if missing:
            raise ContractError(
                f"archive vector {archive_ids[position]!r} is missing {missing}; archive "
                "vectors must be complete joint vectors"
            )
        u_other = model.normalise({name: float(params[name]) for name in model.names})
        rows.append((_distance(u_other, anchor_u), position))
    # Deterministic: distance, then id, so ties never depend on input order.
    rows.sort(key=lambda item: (item[0], str(archive_ids[item[1]])))
    out: list[tuple[np.ndarray, str, tuple[str, ...]]] = []
    for distance, position in rows[: int(neighbour_count)]:
        if distance == 0.0:
            continue  # the anchor itself supplies no direction
        params = archive[position]
        u_other = model.normalise({name: float(params[name]) for name in model.names})
        direction = u_other - anchor_u
        norm = np.linalg.norm(direction)
        if norm == 0.0:
            continue
        out.append((
            direction / norm,
            DIRCLASS_ARCHIVE_DIFF,
            (anchor.anchor_id, str(archive_ids[position])),
        ))
        if len(out) >= int(rank_truncation):
            break
    return out


def confirmed_difference_directions(
    anchor: Anchor,
    model: nc.ModelSpace,
    *,
    confirmed: Sequence[Mapping[str, float]],
    confirmed_ids: Sequence[str],
    rank_truncation: int,
) -> list[tuple[np.ndarray, str, tuple[str, ...]]]:
    """Directions along differences between confirmed vectors (spec sec. 3.2)."""
    if len(confirmed) != len(confirmed_ids):
        raise ContractError("confirmed vectors and ids differ in length")
    anchor_u = anchor.u(model)
    out: list[tuple[np.ndarray, str, tuple[str, ...]]] = []
    order = sorted(range(len(confirmed)), key=lambda i: str(confirmed_ids[i]))
    for position in order:
        params = confirmed[position]
        if str(confirmed_ids[position]) == anchor.anchor_id:
            continue
        u_other = model.normalise({name: float(params[name]) for name in model.names})
        direction = u_other - anchor_u
        norm = np.linalg.norm(direction)
        if norm == 0.0:
            continue
        out.append((
            direction / norm,
            DIRCLASS_CONFIRMED_DIFF,
            (anchor.anchor_id, str(confirmed_ids[position])),
        ))
        if len(out) >= int(rank_truncation):
            break
    return out


def cross_block_directions(
    anchor: Anchor,
    model: nc.ModelSpace,
    *,
    seed: int,
    count: int,
    generator: str = "gaussian_unit_sphere",
) -> list[tuple[np.ndarray, str, tuple[str, ...]]]:
    """Compensating moves that act on exactly two parameter blocks.

    The block partition is the coupled search's own
    (:data:`coupled_calibration_search.BLOCKS`), and the pairs are cycled in
    the frozen :data:`coupled_calibration_search.BLOCK_PAIRS` order, so the
    design does not depend on iteration order anywhere.  ``p``, when present,
    joins the diagnosis block: it is a diagnosis mechanism.
    """
    rng = design_rng(seed, "cross_block", anchor.vector_hash)
    index_of = {name: i for i, name in enumerate(model.names)}
    blocks: dict[str, list[int]] = {}
    for block, members in ccs.BLOCKS.items():
        idx = [index_of[name] for name in members if name in index_of]
        if idx:
            blocks[block] = idx
    if model.p_name is not None:
        blocks.setdefault("diagnosis", []).append(index_of[model.p_name])
    pairs = [pair for pair in ccs.BLOCK_PAIRS if pair[0] in blocks and pair[1] in blocks]
    if not pairs:
        raise ContractError("no usable block pairs for cross-block directions")
    out: list[tuple[np.ndarray, str, tuple[str, ...]]] = []
    for position in range(int(count)):
        left, right = pairs[position % len(pairs)]
        direction = np.zeros(model.n, dtype=float)
        for block, sign in ((left, 1.0), (right, -1.0)):
            idx = np.array(blocks[block], dtype=int)
            part = rng.standard_normal(idx.size)
            norm = np.linalg.norm(part)
            if norm == 0.0:
                part = np.ones(idx.size)
                norm = np.linalg.norm(part)
            direction[idx] = sign * part / norm
        norm = np.linalg.norm(direction)
        out.append((
            direction / norm,
            f"{DIRCLASS_CROSS_BLOCK}:{left}+{right}",
            (anchor.anchor_id,),
        ))
    return out


def directed_probes(
    anchor: Anchor,
    model: nc.ModelSpace,
    validator: Validator,
    *,
    stage: str,
    kernel: str,
    directions: Sequence[tuple[np.ndarray, str, tuple[str, ...]]],
    scales: Sequence[float],
    quota: int,
    seed: int,
    min_anchor_sep: float,
    min_neighbour_sep: float,
    max_attempts: int,
    index_offset: int = 0,
) -> KernelOutcome:
    """Walk declared directions at declared scales, in both senses.

    Paired moves: each direction is tried at ``+scale`` and ``-scale``, which
    is what makes a correlated ridge detectable rather than only its
    one-sided tail.  Order is frozen (direction, scale, sign) so the design is
    reproducible; invalid proposals are skipped, never clipped.
    """
    outcome = KernelOutcome(kernel=kernel, anchor_id=anchor.anchor_id, quota=int(quota))
    if int(quota) == 0:
        return outcome
    if not directions:
        outcome.exhausted = True
        return outcome
    anchor_u = anchor.u(model)
    seen: dict[str, Proposal] = {}
    rng = design_rng(seed, kernel, anchor.vector_hash)
    index = int(index_offset)
    tried = feasible = 0

    for scale in [float(s) for s in scales]:
        for direction, dir_class, parents in directions:
            for sign in (1.0, -1.0):
                if len(outcome.accepted) >= int(quota):
                    break
                if outcome.attempts >= int(max_attempts):
                    break
                outcome.attempts += 1
                tried += 1
                u_new = anchor_u + sign * scale * np.asarray(direction, dtype=float)
                proposal = _make_proposal(
                    stage=stage, model=model, kernel=kernel, anchor=anchor,
                    index=index, u_new=u_new, scale=scale,
                    direction_class=dir_class, parent_ids=parents,
                    validator=validator, rng=rng, attempt_index=outcome.attempts,
                )
                index += 1
                if not proposal.accepted:
                    _register_rejection(outcome, proposal)
                    continue
                feasible += 1
                _accept(
                    outcome, proposal, seen,
                    min_anchor_sep=min_anchor_sep, min_neighbour_sep=min_neighbour_sep,
                    anchor_u=anchor_u, model=model,
                )
    outcome.feasible_direction_coverage = float(feasible) / float(tried) if tried else None
    outcome.exhausted = len(outcome.accepted) < int(quota)
    return outcome


# ==========================================================================
# Kernel 3: M1 p-boundary coverage (spec sec. 3.3)
# ==========================================================================

def p_boundary_probes(
    anchor: Anchor,
    model: nc.ModelSpace,
    validator: Validator,
    *,
    stage: str,
    p_bins: Sequence[float],
    seed: int,
    index_offset: int = 0,
    extra_values: Sequence[float] = (),
) -> KernelOutcome:
    """Explicit ``p = 0`` and one-sided probes at each declared bin edge.

    One-sided by construction: ``p`` cannot go below zero, so the neighbourhood
    at the boundary is one-sided and that is reported rather than read as
    isolation.

    **Every declared bin edge is probed, including edges arbitrarily close to
    zero.**  There is no exclusion band: the specification prohibits one, and
    a positive edge is a valid parameter value whose only special property is
    that it must be compared distributionally rather than paired with ``p=0``.
    ``extra_values`` lets the plan add further declared ``p`` values (for
    example a deliberately tiny positive probe) without widening the bins.
    """
    if model.p_name is None:
        raise ContractError("p-boundary probes require M1")
    outcome = KernelOutcome(kernel=nc.KERNEL_P_BOUNDARY, anchor_id=anchor.anchor_id, quota=0)
    rng = design_rng(seed, "p_boundary", anchor.vector_hash)
    index = int(index_offset)

    wanted: list[float] = [0.0]
    for edge in sorted({float(b) for b in p_bins} | {float(v) for v in extra_values}):
        if edge not in wanted:
            wanted.append(edge)
    outcome.quota = len(wanted)

    for value in wanted:
        params = dict(anchor.params)
        params[model.p_name] = float(value)
        u_new = model.normalise(params)
        outcome.attempts += 1
        proposal = _make_proposal(
            stage=stage, model=model, kernel=nc.KERNEL_P_BOUNDARY, anchor=anchor,
            index=index, u_new=u_new, scale=float(value),
            direction_class=DIRCLASS_P_BOUNDARY, parent_ids=(anchor.anchor_id,),
            validator=validator, rng=rng, attempt_index=outcome.attempts,
        )
        index += 1
        if proposal.accepted:
            outcome.accepted.append(proposal)
        else:
            _register_rejection(outcome, proposal)
    outcome.exhausted = len(outcome.accepted) < outcome.quota
    return outcome


# ==========================================================================
# Kernel 4: bridge probes (spec sec. 4)
# ==========================================================================

def bridge_probes(
    left: Anchor,
    right: Anchor,
    model: nc.ModelSpace,
    validator: Validator,
    *,
    stage: str,
    fractions: Sequence[float],
    seed: int,
    edge_id: str,
    index_offset: int = 0,
) -> KernelOutcome:
    """Interpolation probes along one proposed straight bridge.

    A bridge probe retains **both** endpoints as ordered parents, so no edge
    has to invent a single anchor.  The straight line is a *proposal*: if a
    probe fails, the edge is unresolved, never proof of disconnection, because
    a correlated region may curve between two plausible points.
    """
    outcome = KernelOutcome(
        kernel=nc.KERNEL_BRIDGE, anchor_id=edge_id, quota=len(tuple(fractions))
    )
    rng = design_rng(seed, "bridge", left.vector_hash, right.vector_hash)
    u_left, u_right = left.u(model), right.u(model)
    index = int(index_offset)
    for fraction in [float(f) for f in fractions]:
        if not (0.0 < fraction < 1.0):
            raise ContractError(f"bridge fraction must lie strictly in (0, 1): {fraction}")
        u_new = u_left + fraction * (u_right - u_left)
        outcome.attempts += 1
        proposal = _make_proposal(
            stage=stage, model=model, kernel=nc.KERNEL_BRIDGE, anchor=left,
            index=index, u_new=u_new, scale=fraction,
            direction_class=DIRCLASS_BRIDGE,
            parent_ids=(left.anchor_id, right.anchor_id),
            validator=validator, rng=rng, attempt_index=outcome.attempts,
        )
        index += 1
        # Keyed to the edge, not to one endpoint: a bridge has two parents.
        proposal = replace(
            proposal,
            design_id=(
                f"{stage}/{model.model}/{nc.KERNEL_BRIDGE}/{edge_id}/"
                f"{int(round(fraction * 100)):03d}"
            ),
        )
        if proposal.accepted:
            outcome.accepted.append(proposal)
        else:
            _register_rejection(outcome, proposal)
    outcome.exhausted = len(outcome.accepted) < outcome.quota
    return outcome


# ==========================================================================
# Quota enforcement
# ==========================================================================

def assert_quotas(
    outcomes: Sequence[KernelOutcome],
    quotas: Mapping[str, int],
    *,
    what: str,
) -> None:
    """Every declared kernel quota must be met exactly (spec sec. 3, sec. 5).

    Checked before dispatch and again after merge.  A missing kernel family or
    an unmet quota is fatal: it is an execution defect, not a scientific
    result about the model's geometry.
    """
    produced: dict[str, int] = {}
    for outcome in outcomes:
        produced[outcome.kernel] = produced.get(outcome.kernel, 0) + len(outcome.accepted)
    problems: list[str] = []
    for kernel, quota in quotas.items():
        got = produced.get(kernel, 0)
        if kernel not in produced:
            problems.append(f"kernel family {kernel!r} is entirely missing (quota {quota})")
        elif got != int(quota):
            problems.append(f"kernel {kernel!r} produced {got} proposals, quota is {int(quota)}")
    for outcome in outcomes:
        if outcome.exhausted:
            problems.append(
                f"kernel {outcome.kernel!r} for anchor {outcome.anchor_id!r} exhausted its "
                f"redraw budget after {outcome.attempts} attempts with "
                f"{len(outcome.accepted)}/{outcome.quota} accepted"
            )
    if problems:
        raise KernelExhaustedError(
            f"{what}: kernel quotas are not satisfied; a kernel may not fall back to another "
            "kernel or pad its quota.\n" + "\n".join(f"  - {p}" for p in problems)
        )


def assert_coordinates_vary(
    proposals: Sequence[Proposal],
    model: nc.ModelSpace,
    *,
    expected_varying: Sequence[str] | None = None,
    expected_fixed: Sequence[str] = (),
) -> None:
    """Every coordinate intended to vary must actually vary (spec sec. 9).

    An unexpected zero-variation coordinate is fatal rather than a scientific
    result: it almost always means a kernel silently failed to touch part of
    the space.
    """
    if not proposals:
        raise ContractError("no proposals to check for coordinate variation")
    matrix = np.array(
        [[float(p.params[name]) for name in model.names] for p in proposals], dtype=float
    )
    spread = matrix.max(axis=0) - matrix.min(axis=0)
    varying = set(expected_varying) if expected_varying is not None else set(model.names)
    varying -= set(expected_fixed)
    problems: list[str] = []
    for index, name in enumerate(model.names):
        if name in varying and spread[index] == 0.0:
            problems.append(f"{name} was intended to vary but is constant across all proposals")
        if name in set(expected_fixed) and spread[index] != 0.0:
            problems.append(f"{name} was intended to stay fixed but varies by {spread[index]!r}")
    if problems:
        raise ContractError("coordinate-variation contract violated:\n" + "\n".join(
            f"  - {p}" for p in problems
        ))


__all__ = [name for name in dir() if not name.startswith("_")]
