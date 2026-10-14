#!/usr/bin/env python3
"""Frozen contract for the robust-NROY freeze stage (spec 2026-10-14).

This module holds the *contract*: the config loader that fails closed while
required numerical decisions are unsigned, the model/target/parameter
identities, canonical hashing, the seed registry, the evidence vocabulary and
the promotion/classification rules.

It constructs no simulation engine, runs no simulation and writes no file.

Reference specifications
------------------------
* ``documentation/reports/calibration/PRODUCTION_LOCKIN_SPEC_2026-10-13.md``
* ``documentation/reports/calibration/ROBUST_NROY_FREEZE_SPEC_2026-10-14.md``

Scientific contract, stated once here and enforced throughout
-------------------------------------------------------------
* Acceptance is unchanged: the frozen primary maximum-implausibility rule with
  predictive process variance ``s_i^2 (1 + 1/K)`` and cutoff 3, reusing
  :mod:`hm_implausibility`.  Nothing in this stage may widen or tighten it.
* The local-support labels produced here are *forecast-ensemble eligibility*
  labels.  They are not epidemiological acceptance and they are not claims
  about the topology of the NROY set.
* "No local support detected" is a statement about a declared finite design,
  never a mathematical claim that a point is isolated.
* Euclidean distance proposes candidate edges only.  It never establishes a
  region, and "sampled-path supported" never asserts continuous connectivity.
* Every required numerical decision is either signed in the config or the
  stage refuses to run.  There are no defaults for unsigned science.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

import coupled_calibration_search as ccs
from coupled_calibration_search import ContractError, json_safe, sha256_file, sha256_json

ROOT = Path(__file__).resolve().parent

#: Written into the config wherever a value is a decision Rishi has not signed.
#: Any required field still holding this sentinel makes the stage refuse to run.
UNSIGNED = "UNSIGNED"

#: Accepted spellings of the sentinel, so a hand-edited config cannot slip a
#: placeholder through on a technicality.
UNSIGNED_TOKENS = frozenset({
    "UNSIGNED", "unsigned", "TBD", "tbd", "TODO", "todo",
    "PROPOSED", "proposed", "", "null", "None",
})

CONTRACT_VERSION = "robust_nroy_freeze/1.0.0"


# ==========================================================================
# Evidence vocabulary
# ==========================================================================
#
# The specification requires five mutually exclusive states to be
# distinguishable in every artifact (spec sec. 1, sec. 9 "Reporting").  They are
# strings rather than an Enum so that they round-trip through JSON/CSV without
# a codec.

#: Support was demonstrated under the declared design.
EVIDENCE_CONFIRMED = "confirmed_support"
#: The complete declared design ran and found no qualifying support.
EVIDENCE_NO_SUPPORT = "no_support_detected"
#: Coverage/precision/directional adequacy is insufficient to classify.
EVIDENCE_UNRESOLVED = "unresolved_evidence"
#: The simulation or worker failed.  NOT a scientific result.
EVIDENCE_EXECUTION_FAILURE = "execution_failure"
#: The work unit was never dispatched.  NOT a scientific result.
EVIDENCE_NOT_RUN = "work_not_run"

EVIDENCE_STATES = (
    EVIDENCE_CONFIRMED,
    EVIDENCE_NO_SUPPORT,
    EVIDENCE_UNRESOLVED,
    EVIDENCE_EXECUTION_FAILURE,
    EVIDENCE_NOT_RUN,
)

#: States that are scientific results.  The other two are execution bookkeeping
#: and may never be reported as evidence about the model.
EVIDENCE_SCIENTIFIC = frozenset({EVIDENCE_CONFIRMED, EVIDENCE_NO_SUPPORT, EVIDENCE_UNRESOLVED})

# Local-support labels (spec sec. 3).
LABEL_BROADLY_SUPPORTED = "broadly_supported"
LABEL_RIDGE_SUPPORTED = "ridge_supported"
LABEL_NO_LOCAL_SUPPORT = "no_local_support_detected"
LABEL_UNRESOLVED = "unresolved"
LOCAL_SUPPORT_LABELS = (
    LABEL_BROADLY_SUPPORTED,
    LABEL_RIDGE_SUPPORTED,
    LABEL_NO_LOCAL_SUPPORT,
    LABEL_UNRESOLVED,
)

#: Only these labels make a point eligible for representative selection
#: (spec sec. 1 "Decision consequence").
ELIGIBLE_LABELS = frozenset({LABEL_BROADLY_SUPPORTED, LABEL_RIDGE_SUPPORTED})

# Edge labels (spec sec. 4).
EDGE_SAMPLED_PATH_SUPPORTED = "sampled_path_supported"
EDGE_UNRESOLVED = "unresolved"
EDGE_LABELS = (EDGE_SAMPLED_PATH_SUPPORTED, EDGE_UNRESOLVED)

# Kernel families (spec sec. 3 and sec. 5 "handoff audit").
KERNEL_AMBIENT = "ambient"
KERNEL_ARCHIVE_FOLLOWING = "archive_following"
KERNEL_CROSS_BLOCK = "cross_block"
KERNEL_BRIDGE = "bridge"
KERNEL_P_BOUNDARY = "p_boundary"
KERNEL_FAMILIES = (
    KERNEL_AMBIENT,
    KERNEL_ARCHIVE_FOLLOWING,
    KERNEL_CROSS_BLOCK,
    KERNEL_BRIDGE,
    KERNEL_P_BOUNDARY,
)

# Stage names.  A stage may only parent its exact declared successor.
STAGE_PREPARE = "prepare"
STAGE_DISCOVERY = "discovery"
STAGE_PROMOTION = "promotion"
STAGE_CONFIRMATION = "confirmation"
STAGE_BRIDGES = "bridges"
STAGE_REGIONS = "regions"
STAGE_ORDER = (
    STAGE_PREPARE,
    STAGE_DISCOVERY,
    STAGE_PROMOTION,
    STAGE_CONFIRMATION,
    STAGE_BRIDGES,
    STAGE_REGIONS,
)
#: ``stage -> required exact parent``.  ``None`` means the stage has no parent.
STAGE_PARENT = {
    STAGE_PREPARE: None,
    STAGE_DISCOVERY: STAGE_PREPARE,
    STAGE_PROMOTION: STAGE_DISCOVERY,
    STAGE_CONFIRMATION: STAGE_PROMOTION,
    STAGE_BRIDGES: STAGE_CONFIRMATION,
    STAGE_REGIONS: STAGE_BRIDGES,
}

# Run status vocabulary, extending the coupled-search convention.
STATUS_PREPARED = "PREPARED"
STATUS_COMPLETE = "COMPLETE"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"
ALL_STATUSES = (STATUS_PREPARED, STATUS_COMPLETE, STATUS_PARTIAL, STATUS_FAILED)

#: Only a COMPLETE stage may be used as a parent (spec sec. 5 "Stage handoff").
PARENTABLE_STATUSES = frozenset({STATUS_COMPLETE})

MODEL_M0 = "M0"
MODEL_M1 = "M1"
MODELS = (MODEL_M0, MODEL_M1)


# ==========================================================================
# Canonical serialisation and hashing
# ==========================================================================

def canonical_float(value: float) -> str:
    """The frozen canonical float serialisation used by every vector hash.

    ``repr`` of a Python float is the shortest string that round-trips exactly
    (PEP 3141 / CPython ``float_repr_style`` short).  Using it means two
    vectors share a hash only when every coordinate is bit-for-bit equal, and
    the hash is stable across platforms and numpy versions.

    ``-0.0`` is normalised to ``0.0`` so that a sign-of-zero difference, which
    is scientifically meaningless here, cannot fork an identity.  Non-finite
    values are refused: they are never a legitimate parameter coordinate.
    """
    number = float(value)
    if not np.isfinite(number):
        raise ContractError(f"non-finite parameter coordinate cannot be hashed: {value!r}")
    if number == 0.0:
        number = 0.0
    return repr(number)


def vector_hash(params: Mapping[str, float], *, names: Sequence[str] | None = None) -> str:
    """Canonical hash of a complete joint parameter vector.

    ``names``, when given, is the frozen parameter order and is *required* to
    match the mapping exactly: a missing or extra coordinate is fatal rather
    than hashed around.  The payload embeds the names, so two different spaces
    cannot collide, and embeds the contract version so a change to this
    function cannot silently reuse old hashes.

    This deliberately hashes the *complete joint vector*.  There is no API in
    this module that hashes or reconstructs a vector from marginals.
    """
    if names is None:
        order = sorted(params)
    else:
        order = list(names)
        missing = [name for name in order if name not in params]
        extra = sorted(set(params) - set(order))
        if missing or extra:
            raise ContractError(
                f"vector does not match the frozen parameter order: missing={missing}, extra={extra}"
            )
    payload = "|".join(f"{name}={canonical_float(params[name])}" for name in order)
    return hashlib.sha256(f"{CONTRACT_VERSION}\n{payload}".encode()).hexdigest()


def canonical_vector_payload(params: Mapping[str, float], names: Sequence[str]) -> str:
    """The exact string that :func:`vector_hash` digests.  Exposed for audits."""
    return "|".join(f"{name}={canonical_float(params[name])}" for name in names)


# ==========================================================================
# Fail-closed config loading
# ==========================================================================

def is_unsigned(value: Any) -> bool:
    """Whether ``value`` is an unsigned placeholder rather than a decision."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in UNSIGNED_TOKENS
    if isinstance(value, Mapping):
        return any(is_unsigned(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return len(value) == 0 or any(is_unsigned(item) for item in value)
    return False


@dataclass(frozen=True)
class UnsignedValue:
    """One required decision that is still a proposal."""

    path: str
    description: str
    proposal: Any = None

    def as_row(self) -> dict:
        return {
            "path": self.path,
            "description": self.description,
            "proposal": json_safe(self.proposal),
        }


class UnsignedConfigError(ContractError):
    """The run plan still contains unsigned required decisions.

    This is the stage's fail-closed guard.  It lists *every* unsigned value
    rather than the first, so the sign-off sheet can be completed in one pass.
    """

    def __init__(self, unsigned: Sequence[UnsignedValue]):
        self.unsigned = tuple(unsigned)
        lines = "\n".join(
            f"  - {item.path}: {item.description}"
            + (f" (proposal: {item.proposal!r})" if item.proposal is not None else "")
            for item in self.unsigned
        )
        super().__init__(
            f"{len(self.unsigned)} required value(s) are unsigned; no simulation may start.\n"
            f"{lines}\n"
            "Sign each value in the run plan (and in section 5 of "
            "PRODUCTION_LOCKIN_SPEC_2026-10-13.md) before requesting a production run."
        )


#: Every required decision, as ``dotted.path -> human description``.
#:
#: These are exactly the values the production specification lists as "numbers
#: still to set before running" (sec. 3) plus the freeze-stage settings the
#: NROY specification requires to be frozen before simulation (sec. 3-5).
#: Nothing here has a default.  Adding a default to any of these would defeat
#: the fail-closed guard, which is the point of the module.
REQUIRED_DECISIONS: dict[str, str] = {
    # ---- production lock-in spec, section 3 ----
    "target_contract.register_path": "production target register path",
    "target_contract.frozen_order": "exact frozen target order",
    "target_contract.n_targets": "declared number of targets (contract size)",
    "p_parameter.name": "calibration-space name of the AIDS-symptom diagnosis probability p",
    "p_parameter.min": "lower end of the p range",
    "p_parameter.max": "upper end of the p range",
    "p_parameter.bins": "predeclared p coverage bins",
    "p_parameter.routine_hazard_scale_identity_value": (
        "routine_hazard_scale pinned for M1; the engine's p=0 identity dispatch requires it"
    ),
    "p_parameter.identity_epsilon": (
        "exclusion half-width above exact p=0; the engine dispatches on p == 0.0 exactly, "
        "so probes in (0, epsilon) are discontinuous and must be rejected, not snapped"
    ),
    "budget.max_trajectories": "trajectory budget for this stage",
    "budget.max_attempts": "launch-attempt budget (attempts consume budget)",
    "budget.wall_time_cap_seconds": "wall-clock cap before the stage is PARTIAL",
    "representative_selection.rule": "representative-selection rule (geometry only)",
    "representative_selection.n_per_component": "number of representatives per component",
    # ---- NROY freeze spec, section 3: local designs ----
    "ambient.scales": "ambient probe scales in the normalised metric",
    "ambient.scale_semantics": "whether a scale is a radius or a coordinate-wise bound",
    "ambient.quota_per_scale": "ambient probe quota per scale per anchor",
    "ambient.direction_generator": "declared ambient direction generator",
    "archive_following.quota": "correlation-following probe quota per anchor",
    "archive_following.scales": "correlation-following probe scales",
    "archive_following.neighbour_rule": "archive neighbour rule",
    "archive_following.rank_truncation": "rank truncation for archive directions",
    "archive_following.source_pool": "declared source pool for archive directions",
    "cross_block.quota": "cross-block probe quota per anchor",
    "cross_block.scales": "cross-block probe scales",
    "separation.min_anchor_neighbour": "minimum anchor-neighbour separation",
    "separation.min_neighbour_neighbour": "minimum neighbour-neighbour separation",
    "separation.min_direction_classes": "independent direction classes required for ridge support",
    "promotion.variance_alpha": "alpha for the process-variance upper confidence bound",
    "support.min_confirming_neighbours": "neighbours required to declare support",
    # ---- NROY freeze spec, section 4: graph ----
    "graph.neighbour_rule": "candidate-edge rule for the provisional graph",
    "graph.neighbour_count": "neighbour count for the provisional graph",
    "graph.tie_breaking": "deterministic tie-breaking rule",
    "graph.minimal_edge_algorithm": "minimal auditable edge-set algorithm",
    "graph.bridge_fractions": "frozen bridge interpolation fractions",
    "graph.bridge_pass_rule": "rule requiring every bridge probe to confirm",
    # ---- seeds ----
    "seeds.discovery": "discovery seed block (five common seeds)",
    "seeds.confirmation": "reserved confirmation seed block (20 disjoint fresh seeds)",
    "seeds.proposal_rng": "design RNG seed",
}


def _dig(mapping: Mapping, path: str) -> tuple[bool, Any]:
    """Resolve a dotted path.  Returns ``(found, value)``."""
    node: Any = mapping
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False, None
        node = node[part]
    return True, node


def unsigned_values(plan: Mapping, *, required: Mapping[str, str] | None = None) -> list[UnsignedValue]:
    """Every required decision in ``plan`` that is missing or still a proposal."""
    requirements = dict(REQUIRED_DECISIONS if required is None else required)
    out: list[UnsignedValue] = []
    for path, description in requirements.items():
        found, value = _dig(plan, path)
        if not found or is_unsigned(value):
            _, proposal = _dig(plan, f"{path}_proposal")
            out.append(UnsignedValue(path=path, description=description, proposal=proposal))
    return out


def assert_fully_signed(plan: Mapping, *, required: Mapping[str, str] | None = None) -> None:
    """Refuse to continue while any required decision is unsigned."""
    pending = unsigned_values(plan, required=required)
    if pending:
        raise UnsignedConfigError(pending)


def signature_block(plan: Mapping) -> dict:
    """The recorded sign-off, validated.

    A signed plan must name who signed it and when.  An unsigned-but-complete
    numbers section is still not a greenlight: the specification requires the
    sign-off itself to be recorded.
    """
    found, block = _dig(plan, "sign_off")
    if not found or not isinstance(block, Mapping):
        raise ContractError("run plan has no 'sign_off' block")
    missing = [key for key in ("signed_by", "signed_date", "spec_version")
               if is_unsigned(block.get(key))]
    if missing:
        raise ContractError(f"sign_off block is incomplete: {missing}")
    return dict(block)


# ==========================================================================
# Target contract
# ==========================================================================

@dataclass(frozen=True)
class TargetContract:
    """The frozen target register, order and reporting groups.

    M0 and M1 must use the *same* contract (spec sec. 2).  The order is frozen:
    every residual vector, score table and stored matrix uses exactly this
    order, and a reordering is fatal rather than silently corrected.
    """

    frozen_order: tuple[str, ...]
    register_path: str
    register_sha256: str
    groups: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.frozen_order:
            raise ContractError("target contract has no targets")
        if len(set(self.frozen_order)) != len(self.frozen_order):
            duplicated = sorted({t for t in self.frozen_order if self.frozen_order.count(t) > 1})
            raise ContractError(f"target contract repeats targets: {duplicated}")
        if self.groups:
            flat = [t for group in self.groups.values() for t in group]
            if len(flat) != len(set(flat)):
                raise ContractError("reporting groups overlap")
            unknown = sorted(set(flat) - set(self.frozen_order))
            if unknown:
                raise ContractError(f"reporting groups name unknown targets: {unknown}")
            uncovered = sorted(set(self.frozen_order) - set(flat))
            if uncovered:
                raise ContractError(f"reporting groups do not cover: {uncovered}")

    @property
    def n(self) -> int:
        return len(self.frozen_order)

    @classmethod
    def from_plan(cls, plan: Mapping, *, root: Path = ROOT) -> "TargetContract":
        node = plan.get("target_contract")
        if not isinstance(node, Mapping):
            raise ContractError("run plan has no 'target_contract' block")
        order = tuple(str(t) for t in node["frozen_order"])
        declared_n = int(node["n_targets"])
        if declared_n != len(order):
            raise ContractError(
                f"target contract declares n_targets={declared_n} but lists {len(order)} targets"
            )
        register_path = str(node["register_path"])
        resolved = (root / register_path) if not Path(register_path).is_absolute() else Path(register_path)
        if not resolved.exists():
            raise ContractError(f"target register does not exist: {register_path}")
        actual = sha256_file(resolved)
        expected = node.get("register_sha256")
        if not is_unsigned(expected) and str(expected) != actual:
            raise ContractError(
                f"target register hash mismatch for {register_path}: "
                f"expected {expected}, found {actual}"
            )
        groups = {
            str(name): tuple(str(t) for t in members)
            for name, members in (node.get("reporting_groups") or {}).items()
        }
        return cls(
            frozen_order=order,
            register_path=register_path,
            register_sha256=actual,
            groups=groups,
        )

    def assert_matches(self, other: "TargetContract", *, what: str) -> None:
        """M0/M1 and parent/child must share the contract exactly."""
        if self.frozen_order != other.frozen_order:
            raise ContractError(
                f"{what}: target order differs.\n"
                f"  this:  {list(self.frozen_order)}\n"
                f"  other: {list(other.frozen_order)}"
            )
        if self.register_sha256 != other.register_sha256:
            raise ContractError(
                f"{what}: target register hash differs "
                f"({self.register_sha256} vs {other.register_sha256})"
            )

    def assert_order(self, observed: Sequence[str], *, what: str = "targets") -> None:
        """Fail closed on missing, extra or silently reordered targets."""
        seen = tuple(str(t) for t in observed)
        if seen == self.frozen_order:
            return
        missing = [t for t in self.frozen_order if t not in seen]
        extra = [t for t in seen if t not in self.frozen_order]
        if missing or extra:
            raise ContractError(
                f"{what}: target set does not match the contract: missing={missing}, extra={extra}"
            )
        raise ContractError(
            f"{what}: targets are reordered relative to the frozen contract.\n"
            f"  frozen:   {list(self.frozen_order)}\n"
            f"  observed: {list(seen)}"
        )

    def hash(self) -> str:
        return sha256_json({
            "frozen_order": list(self.frozen_order),
            "register_sha256": self.register_sha256,
            "groups": {k: list(v) for k, v in sorted(self.groups.items())},
        })


# ==========================================================================
# Model space: M0 (33) and M1 (34, adding p) with exact nesting
# ==========================================================================

@dataclass(frozen=True)
class ModelSpace:
    """A model's parameter space, its frozen name order and the p coordinate.

    M1 is M0 plus one coordinate.  The M0 names keep their original order and
    ``p`` is appended last, so an M0 vector embeds into M1 by appending one
    value and the shared coordinates keep their indices.  That is what makes
    the ``p = 0`` identity check a pure extension rather than a re-mapping.
    """

    model: str
    space: ccs.ParameterSpace
    p_name: str | None
    p_min: float | None
    p_max: float | None

    def __post_init__(self) -> None:
        if self.model not in MODELS:
            raise ContractError(f"unknown model {self.model!r}; expected one of {MODELS}")
        if self.model == MODEL_M0 and self.p_name is not None:
            raise ContractError("M0 must not carry a p coordinate")
        if self.model == MODEL_M1:
            if self.p_name is None:
                raise ContractError("M1 requires a p coordinate")
            if self.p_name not in self.space.names:
                raise ContractError(
                    f"p parameter {self.p_name!r} is not in the M1 calibration space"
                )
            if self.space.names[-1] != self.p_name:
                raise ContractError(
                    f"p parameter {self.p_name!r} must be the last coordinate of M1, "
                    f"found {self.space.names[-1]!r}"
                )
            lower = float(self.space.lower[self.p_index])
            if lower != 0.0:
                raise ContractError(
                    f"the M1 p range must include exact p=0 as its lower bound; found {lower!r}"
                )

    @property
    def names(self) -> tuple[str, ...]:
        return self.space.names

    @property
    def n(self) -> int:
        return self.space.n

    @property
    def p_index(self) -> int:
        if self.p_name is None:
            raise ContractError(f"{self.model} has no p coordinate")
        return self.space.names.index(self.p_name)

    def base_names(self) -> tuple[str, ...]:
        """The names shared with M0, in order."""
        if self.p_name is None:
            return self.space.names
        return tuple(n for n in self.space.names if n != self.p_name)

    def vector_hash(self, params: Mapping[str, float]) -> str:
        return vector_hash(params, names=self.names)

    def normalise(self, x) -> np.ndarray:
        return self.space.normalise(x)

    def denormalise(self, u) -> np.ndarray:
        return self.space.denormalise(u)

    def as_dict(self, x) -> dict[str, float]:
        return self.space.as_dict(x)


#: The block the ``p`` coordinate belongs to.  ``p`` is a diagnosis mechanism,
#: so it joins the diagnosis block rather than forming a block of its own.
P_BLOCK = "diagnosis"


def model_blocks(model: "ModelSpace") -> dict[str, tuple[str, ...]]:
    """The block partition for this model, with ``p`` folded into diagnosis."""
    blocks = {name: tuple(members) for name, members in ccs.BLOCKS.items()}
    if model.p_name is not None:
        blocks[P_BLOCK] = blocks[P_BLOCK] + (model.p_name,)
    return blocks


def load_model_space(
    calib_space: Mapping,
    *,
    model: str,
    p_name: str | None = None,
    p_min: float | None = None,
    p_max: float | None = None,
) -> "ModelSpace":
    """Build a :class:`ModelSpace`, validating an M1-aware block partition.

    :meth:`coupled_calibration_search.ParameterSpace.from_calib_space` asserts
    the *33-name* block partition, which M1 cannot satisfy.  This builds the
    same ``ParameterSpace`` and then checks the partition against
    :func:`model_blocks`, so the block contract is enforced for both models
    rather than skipped for M1.
    """
    parameters = tuple(dict(p) for p in calib_space["parameters"])
    names = tuple(str(p["name"]) for p in parameters)
    if len(set(names)) != len(names):
        raise ContractError("calibration space has duplicate parameter names")
    lower = np.array([float(p["min"]) for p in parameters], dtype=float)
    upper = np.array([float(p["max"]) for p in parameters], dtype=float)
    if not np.all(np.isfinite(lower)) or not np.all(np.isfinite(upper)):
        raise ContractError("calibration space has non-finite bounds")
    if np.any(upper <= lower):
        bad = [names[i] for i in np.flatnonzero(upper <= lower)]
        raise ContractError(f"calibration space has empty bounds for: {bad}")

    space = ccs.ParameterSpace(
        names=names,
        lower=lower,
        upper=upper,
        parameters=parameters,
        config_paths=tuple(str(p["config_path"]) for p in parameters),
    )
    resolved_p = p_name if model == MODEL_M1 else None
    model_space = ModelSpace(
        model=model,
        space=space,
        p_name=resolved_p,
        p_min=None if p_min is None else float(p_min),
        p_max=None if p_max is None else float(p_max),
    )

    flat: list[str] = []
    for members in model_blocks(model_space).values():
        flat.extend(members)
    if len(flat) != len(set(flat)):
        duplicated = sorted({n for n in flat if flat.count(n) > 1})
        raise ContractError(f"block partition repeats parameters: {duplicated}")
    if set(flat) != set(names):
        missing = sorted(set(names) - set(flat))
        extra = sorted(set(flat) - set(names))
        raise ContractError(
            f"block partition does not equal the {model} space: missing={missing}, extra={extra}"
        )
    return model_space


def build_m1_calib_space(
    m0_calib_space: Mapping,
    *,
    p_name: str,
    p_config_path: str,
    p_min: float,
    p_max: float,
    description_suffix: str = "",
) -> dict:
    """Derive the M1 calibration space from M0 by appending the ``p`` coordinate.

    M1 must be M0 *plus one coordinate*, nothing else.  Generating the space
    rather than hand-editing it is what guarantees that: the M0 parameter list,
    its order, its bounds, its fixed and excluded parameters and its constraints
    are all carried over byte-for-byte, and ``p`` is appended last.

    The caller supplies a signed ``p`` range.  ``p_min`` must be exactly 0 so
    that M1 contains the M0 boundary.
    """
    if p_name in {str(p["name"]) for p in m0_calib_space["parameters"]}:
        raise ContractError(f"{p_name!r} is already a parameter of the M0 space")
    if float(p_min) != 0.0:
        raise ContractError(
            f"the M1 p range must start at exact 0 so that M1 nests M0; got {p_min!r}"
        )
    if not (float(p_max) > float(p_min)):
        raise ContractError(f"empty p range [{p_min}, {p_max}]")
    out = json.loads(json.dumps(m0_calib_space))  # deep copy, no shared state
    out["parameters"] = list(out["parameters"]) + [{
        "name": str(p_name),
        "config_path": str(p_config_path),
        "min": float(p_min),
        "max": float(p_max),
        "distribution": "uniform",
        "unit": "annual probability",
        "stages": ["joint"],
    }]
    base_description = str(out.get("description", ""))
    out["description"] = (
        f"{base_description} | M1: M0 plus the annual AIDS-symptom diagnosis probability "
        f"{p_name!r} appended as the last coordinate, range [{float(p_min)}, {float(p_max)}]. "
        f"At exact p=0 the engine's identity dispatch runs the legacy M0 diagnosis path."
        + (f" {description_suffix}" if description_suffix else "")
    )
    out["derived_from_m0"] = {
        "n_parameters_m0": len(m0_calib_space["parameters"]),
        "n_parameters_m1": len(out["parameters"]),
        "p_name": str(p_name),
        "generated_by": "nroy_contract.build_m1_calib_space",
    }
    return out


def embed_m0_in_m1(
    m0: ModelSpace,
    m1: ModelSpace,
    m0_params: Mapping[str, float],
    *,
    p_value: float = 0.0,
) -> dict[str, float]:
    """Carry a confirmed M0 vector into M1 at an exact ``p`` value.

    This is the required identity/control design of the production spec
    (sec. 1 step 4), not an optional warm start.  The complete joint M0 vector
    is carried across unchanged; nothing is re-drawn, re-fitted, rounded or
    reconstructed from marginals.

    ``p_value`` defaults to exact ``0.0``.  It is compared against the literal
    float, so a value such as ``1e-18`` is refused rather than treated as zero.
    """
    if m0.model != MODEL_M0 or m1.model != MODEL_M1:
        raise ContractError(f"embed_m0_in_m1 expects (M0, M1), got ({m0.model}, {m1.model})")
    base = m0.names
    if m1.base_names() != base:
        raise ContractError(
            "M1 is not a pure extension of M0.\n"
            f"  M0 names:            {list(base)}\n"
            f"  M1 names without p:  {list(m1.base_names())}"
        )
    missing = [name for name in base if name not in m0_params]
    extra = sorted(set(m0_params) - set(base))
    if missing or extra:
        raise ContractError(
            f"M0 vector does not match the M0 space: missing={missing}, extra={extra}"
        )
    value = float(p_value)
    lower = float(m1.space.lower[m1.p_index])
    upper = float(m1.space.upper[m1.p_index])
    if not (lower <= value <= upper):
        raise ContractError(f"p={value!r} is outside the declared M1 range [{lower}, {upper}]")
    out = {name: float(m0_params[name]) for name in base}
    out[m1.p_name] = value
    return out


def is_exact_p_zero(params: Mapping[str, float], m1: ModelSpace) -> bool:
    """Whether this M1 vector sits at *exactly* ``p = 0``."""
    if m1.p_name not in params:
        raise ContractError(f"vector has no {m1.p_name!r} coordinate")
    return float(params[m1.p_name]) == 0.0


#: Reason code recorded when a probe lands in the discontinuous p band.
REJECT_P_DISCONTINUITY = "p_in_identity_discontinuity_band"


def p_discontinuity_reason(
    params: Mapping[str, float],
    m1: ModelSpace,
    *,
    identity_epsilon: float,
) -> str | None:
    """Why this M1 vector must be rejected for sitting just above ``p = 0``.

    The engine dispatches on ``p == 0.0`` *exactly*
    (``run_v3.py`` ``_step_diagnosis`` -> ``StageStructuredDiagnosis.is_identity``).
    At exactly zero the legacy diagnosis code runs with the legacy counter-based
    RNG streams; at any ``p > 0`` a different set of named streams is drawn
    from, so the realised trajectory changes discontinuously even though the
    step probability is continuous in ``p``.

    A probe at, say, ``p = 1e-18`` is therefore scientifically meaningless: it
    is not "M0 plus a negligible hazard", it is a different random path.  Such a
    probe is **rejected and redrawn**, with this reason recorded.  It is never
    snapped to zero and never clipped, because silently moving a proposal is
    exactly what the specification forbids.

    Returns ``None`` when the vector is acceptable.
    """
    epsilon = float(identity_epsilon)
    if epsilon < 0.0:
        raise ContractError(f"identity_epsilon must be non-negative, got {identity_epsilon!r}")
    value = float(params[m1.p_name])
    if value == 0.0:
        return None
    if value < epsilon:
        return (
            f"{REJECT_P_DISCONTINUITY}: p={value!r} lies in (0, {epsilon}) where the engine's "
            "exact p==0 identity dispatch makes the trajectory discontinuous; "
            "redraw rather than snap"
        )
    return None


def assert_m1_identity_preconditions(
    *,
    routine_hazard_scale: float,
    required_value: float,
) -> None:
    """The non-``p`` precondition for the M0/M1 identity.

    ``StageStructuredDiagnosis.is_identity`` is
    ``routine_hazard_scale == 1.0 and aids_symptom_annual_probability == 0.0``.
    Both halves matter: ``p = 0`` with a rescaled routine hazard does *not*
    execute the legacy path, so M1 would not nest M0.  M1 therefore pins the
    routine hazard scale, and this check refuses a config that does not.
    """
    actual = float(routine_hazard_scale)
    expected = float(required_value)
    if actual != expected:
        raise ContractError(
            f"M1 requires routine_hazard_scale == {expected!r} for the p=0 identity to hold; "
            f"found {actual!r}. At p=0 with a rescaled routine hazard the engine takes the "
            "stage-structured branch and M1 does not nest M0."
        )


def assert_nesting_inputs_identical(
    m0_params: Mapping[str, float],
    m1_params: Mapping[str, float],
    m1: ModelSpace,
) -> None:
    """Every non-``p`` coordinate must be bit-identical for an identity test.

    The specification requires the identity test to fail deliberately when any
    non-``p`` input differs (sec. 9).  This is the check that makes that
    failure explicit rather than a silent numerical discrepancy.
    """
    differing: list[str] = []
    for name in m1.base_names():
        if name not in m0_params:
            raise ContractError(f"M0 vector is missing {name!r}")
        if name not in m1_params:
            raise ContractError(f"M1 vector is missing {name!r}")
        if canonical_float(m0_params[name]) != canonical_float(m1_params[name]):
            differing.append(name)
    if differing:
        raise ContractError(
            "M0/M1 identity requires every non-p coordinate to be bit-identical; "
            f"these differ: {differing}"
        )
    if not is_exact_p_zero(m1_params, m1):
        raise ContractError(
            f"M0/M1 identity is only defined at exact p=0; got p={m1_params[m1.p_name]!r}"
        )


# ==========================================================================
# Seed registry
# ==========================================================================

@dataclass(frozen=True)
class SeedRegistry:
    """The frozen seed blocks and their disjointness guarantees.

    Discovery uses five *common* seeds shared across candidates (CRN).  The
    confirmation block is reserved: it is not inspected during promotion and is
    common to an anchor and its neighbours, so a neighbour cannot be confirmed
    on a luckier seed set than its anchor.
    """

    discovery: tuple[int, ...]
    confirmation: tuple[int, ...]
    proposal_rng: int
    foreign: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        for label, block in (("discovery", self.discovery), ("confirmation", self.confirmation)):
            if not block:
                raise ContractError(f"{label} seed block is empty")
            if len(set(block)) != len(block):
                raise ContractError(f"{label} seed block repeats seeds")
        overlap = sorted(set(self.discovery) & set(self.confirmation))
        if overlap:
            raise ContractError(
                f"discovery and confirmation seed blocks overlap: {overlap}. "
                "Confirmation must use fresh, disjoint seeds."
            )
        if self.proposal_rng in set(self.discovery) | set(self.confirmation):
            raise ContractError(
                f"the design RNG seed {self.proposal_rng} is also a simulation seed"
            )
        clash = sorted((set(self.discovery) | set(self.confirmation)) & set(self.foreign))
        if clash:
            raise ContractError(
                f"these seeds were already used by an earlier experiment: {clash}. "
                "Production seeds must be disjoint from previous runs."
            )

    @property
    def k_discovery(self) -> int:
        return len(self.discovery)

    @property
    def k_confirmation(self) -> int:
        return len(self.confirmation)

    @classmethod
    def from_plan(cls, plan: Mapping) -> "SeedRegistry":
        node = plan.get("seeds")
        if not isinstance(node, Mapping):
            raise ContractError("run plan has no 'seeds' block")
        return cls(
            discovery=tuple(int(s) for s in node["discovery"]),
            confirmation=tuple(int(s) for s in node["confirmation"]),
            proposal_rng=int(node["proposal_rng"]),
            foreign=tuple(int(s) for s in (node.get("known_used_elsewhere") or ())),
        )

    def seeds_for(self, stage: str) -> tuple[int, ...]:
        if stage in (STAGE_DISCOVERY, STAGE_PROMOTION):
            return self.discovery
        if stage in (STAGE_CONFIRMATION, STAGE_BRIDGES):
            return self.confirmation
        raise ContractError(f"stage {stage!r} has no declared seed block")

    def as_dict(self) -> dict:
        return {
            "discovery": list(self.discovery),
            "confirmation": list(self.confirmation),
            "proposal_rng": self.proposal_rng,
            "k_discovery": self.k_discovery,
            "k_confirmation": self.k_confirmation,
            "design": (
                "common random numbers shared across candidates within a stage; "
                "confirmation seeds reserved, disjoint from discovery, not inspected "
                "during promotion, and common to an anchor and its neighbours"
            ),
        }


# ==========================================================================
# Acceptance, promotion and local-support classification
# ==========================================================================

def predictive_variance(
    base_variance: Mapping[str, float],
    sample_variance: Mapping[str, float],
    k: int,
    *,
    target_order: Sequence[str],
    inflation: float = 1.0,
) -> dict[str, float]:
    """``B_i + inflation * s_i^2 (1 + 1/K)``, the repository's frozen budget.

    ``inflation`` is the chi-square factor of the process-variance confidence
    bound.  ``1.0`` is the nominal budget.  Reusing one function for both means
    the promotion safeguard cannot drift from the nominal acceptance rule.
    """
    if int(k) < 2:
        raise ContractError(f"predictive variance needs K>=2 replicates, got {k!r}")
    factor = float(inflation) * (1.0 + 1.0 / float(int(k)))
    out: dict[str, float] = {}
    for name in target_order:
        if name not in base_variance:
            raise ContractError(f"no frozen base variance for target {name!r}")
        if name not in sample_variance:
            raise ContractError(f"no sample variance for target {name!r}")
        out[name] = float(base_variance[name]) + float(sample_variance[name]) * factor
    return out


def variance_bound_factor(k: int, alpha: float) -> float:
    """Upper ``1-alpha`` confidence factor for ``sigma^2`` from ``K`` replicates.

    ``(K-1) s^2 / sigma^2 ~ chi^2_{K-1}``, so the upper bound multiplies ``s^2``
    by ``(K-1)/chi2.ppf(alpha/2, K-1)``.  This is the same construction as
    ``run_history_matching.predictive_variance_stability``, reused rather than
    reinvented so the promotion safeguard and the published variance-stability
    diagnostic cannot disagree.
    """
    from scipy.stats import chi2  # noqa: PLC0415 - optional-at-import, as elsewhere in the repo

    k_int = int(k)
    if k_int < 2:
        raise ContractError(f"a variance bound needs K>=2 replicates, got {k!r}")
    a = float(alpha)
    if not (0.0 < a < 1.0):
        raise ContractError(f"alpha must be in (0, 1), got {alpha!r}")
    df = k_int - 1
    return float(df / chi2.ppf(a / 2.0, df))


@dataclass(frozen=True)
class PromotionDecision:
    """Why a probe was or was not promoted to the confirmation block."""

    promoted: bool
    nominal_pass: bool
    bound_pass: bool
    i_max_nominal: float
    i_max_bound: float
    variance_unstable: bool
    binding_target: str | None

    def as_row(self) -> dict:
        return {
            "promoted": bool(self.promoted),
            "nominal_pass": bool(self.nominal_pass),
            "bound_pass": bool(self.bound_pass),
            "i_max_nominal": float(self.i_max_nominal),
            "i_max_bound": float(self.i_max_bound),
            "variance_unstable": bool(self.variance_unstable),
            "binding_target": self.binding_target,
        }


def promotion_decision(
    i_max_nominal: float,
    i_max_bound: float,
    *,
    cutoff: float,
    binding_target: str | None = None,
) -> PromotionDecision:
    """The frozen promotion rule (spec sec. 3).

    A probe is promoted if its nominal discovery ``I_max <= cutoff`` *or* if
    rescoring with the upper confidence bound for process variance gives
    ``I_max <= cutoff``.  The bound route is a conservative compute-allocation
    safeguard against five-seed false negatives.  **It is not acceptance.**
    Final acceptance is always recomputed nominally from the reserved
    confirmation block.

    A probe promoted only via the bound is flagged ``variance_unstable`` so the
    distinction stays visible downstream.
    """
    bound = float(cutoff)
    nominal_value = float(i_max_nominal)
    bound_value = float(i_max_bound)
    # Inflating the variance budget DIVIDES the residual by a larger number, so
    # the bounded implausibility must be <= nominal.  A bounded value that is
    # larger means the bound was applied the wrong way round, which would turn
    # the safeguard into a hidden tightening of acceptance.
    if np.isfinite(bound_value) and np.isfinite(nominal_value) and bound_value > nominal_value:
        raise ContractError(
            "the upper variance bound produced a LARGER implausibility than nominal "
            f"({bound_value} > {nominal_value}); inflating process variance can only "
            "decrease implausibility, so the bound must be conservative"
        )
    nominal_pass = bool(np.isfinite(nominal_value) and nominal_value <= bound)
    bound_pass = bool(np.isfinite(bound_value) and bound_value <= bound)
    return PromotionDecision(
        promoted=nominal_pass or bound_pass,
        nominal_pass=nominal_pass,
        bound_pass=bound_pass,
        i_max_nominal=nominal_value,
        i_max_bound=bound_value,
        variance_unstable=bound_pass and not nominal_pass,
        binding_target=binding_target,
    )


@dataclass(frozen=True)
class NeighbourEvidence:
    """One evaluated neighbour of an anchor, as it bears on local support."""

    design_id: str
    kernel: str
    scale: float
    direction_class: str
    vector_hash: str
    distance: float
    evidence: str
    confirmed: bool

    def __post_init__(self) -> None:
        if self.evidence not in EVIDENCE_STATES:
            raise ContractError(f"unknown evidence state {self.evidence!r}")
        if self.kernel not in KERNEL_FAMILIES:
            raise ContractError(f"unknown kernel {self.kernel!r}")
        if self.confirmed and self.evidence != EVIDENCE_CONFIRMED:
            raise ContractError(
                f"neighbour {self.design_id} is marked confirmed but its evidence state "
                f"is {self.evidence!r}"
            )


@dataclass(frozen=True)
class LocalSupport:
    """The local-support classification of one anchor, with its counts."""

    label: str
    reason: str
    n_confirmed_ambient_small: int
    n_confirmed_ambient: int
    n_confirmed_correlation: int
    n_direction_classes: int
    n_evaluated: int
    n_failed: int
    n_not_run: int
    feasible_direction_coverage: float | None = None
    redraw_exhausted: bool = False

    def __post_init__(self) -> None:
        if self.label not in LOCAL_SUPPORT_LABELS:
            raise ContractError(f"unknown local-support label {self.label!r}")

    @property
    def eligible(self) -> bool:
        """Whether this anchor may enter representative selection."""
        return self.label in ELIGIBLE_LABELS

    def as_row(self) -> dict:
        return {
            "local_support_label": self.label,
            "local_support_reason": self.reason,
            "eligible_for_representation": bool(self.eligible),
            "n_confirmed_ambient_small_scale": self.n_confirmed_ambient_small,
            "n_confirmed_ambient": self.n_confirmed_ambient,
            "n_confirmed_correlation_following": self.n_confirmed_correlation,
            "n_independent_direction_classes": self.n_direction_classes,
            "n_neighbours_evaluated": self.n_evaluated,
            "n_neighbours_failed": self.n_failed,
            "n_neighbours_not_run": self.n_not_run,
            "feasible_direction_coverage": self.feasible_direction_coverage,
            "redraw_exhausted": bool(self.redraw_exhausted),
        }


def classify_local_support(
    neighbours: Sequence[NeighbourEvidence],
    *,
    smaller_ambient_scale: float,
    min_confirming: int,
    min_direction_classes: int,
    designed_neighbours: int,
    feasible_direction_coverage: float | None = None,
    redraw_exhausted: bool = False,
    variance_unstable_only: bool = False,
) -> LocalSupport:
    """Classify an anchor's local support (spec sec. 3).

    The ordering of the checks matters and is itself part of the contract:

    1. If the declared design did not actually complete -- any probe failed to
       execute or was never run -- the result is **unresolved**.  An execution
       failure must never read as "no support".
    2. If support is found, label it ``broadly_supported`` (requires an ambient
       neighbour at the smaller scale) or ``ridge_supported``.
    3. Only if the complete design ran and found nothing do we reach
       ``no_local_support_detected``; and even then, a design that was
       directionally inadequate, bound-limited or variance-unstable is
       **unresolved** instead.  A negative label is reserved for a design that
       genuinely had the opportunity to find support.
    """
    unique: dict[str, NeighbourEvidence] = {}
    for item in neighbours:
        # Only unique vector hashes count as neighbours (spec sec. 3).
        unique.setdefault(item.vector_hash, item)
    evidence = list(unique.values())

    n_failed = sum(1 for n in evidence if n.evidence == EVIDENCE_EXECUTION_FAILURE)
    n_not_run = sum(1 for n in evidence if n.evidence == EVIDENCE_NOT_RUN)
    confirmed = [n for n in evidence if n.confirmed]

    ambient = [n for n in confirmed if n.kernel == KERNEL_AMBIENT]
    ambient_small = [n for n in ambient if float(n.scale) <= float(smaller_ambient_scale)]
    correlation = [
        n for n in confirmed
        if n.kernel in (KERNEL_ARCHIVE_FOLLOWING, KERNEL_CROSS_BLOCK)
    ]
    direction_classes = {n.direction_class for n in correlation}

    counts = {
        "n_confirmed_ambient_small": len(ambient_small),
        "n_confirmed_ambient": len(ambient),
        "n_confirmed_correlation": len(correlation),
        "n_direction_classes": len(direction_classes),
        "n_evaluated": len(evidence) - n_not_run,
        "n_failed": n_failed,
        "n_not_run": n_not_run,
        "feasible_direction_coverage": feasible_direction_coverage,
        "redraw_exhausted": redraw_exhausted,
    }

    incomplete = n_failed > 0 or n_not_run > 0 or len(evidence) < int(designed_neighbours)

    # 2. Positive findings first, but only on a design that ran.
    if len(ambient) >= int(min_confirming) and ambient_small:
        return LocalSupport(
            label=LABEL_BROADLY_SUPPORTED,
            reason=(
                f"{len(ambient)} distinct ambient neighbours confirmed, including "
                f"{len(ambient_small)} at the smaller declared scale "
                f"({smaller_ambient_scale})"
            ),
            **counts,
        )
    if (
        len(correlation) >= int(min_confirming)
        and len(direction_classes) >= int(min_direction_classes)
    ):
        return LocalSupport(
            label=LABEL_RIDGE_SUPPORTED,
            reason=(
                f"{len(correlation)} correlation-following neighbours confirmed across "
                f"{len(direction_classes)} independent direction classes; no qualifying "
                "ambient support"
            ),
            **counts,
        )

    # 1./3. Anything short of a complete, adequate, stable design is unresolved.
    if incomplete:
        return LocalSupport(
            label=LABEL_UNRESOLVED,
            reason=(
                f"the declared design did not complete: {n_failed} execution failure(s), "
                f"{n_not_run} not run, {len(evidence)} of {int(designed_neighbours)} unique "
                "probes present"
            ),
            **counts,
        )
    if redraw_exhausted:
        return LocalSupport(
            label=LABEL_UNRESOLVED,
            reason="constraint redraws were exhausted, so the neighbourhood was not fully probed",
            **counts,
        )
    if variance_unstable_only:
        return LocalSupport(
            label=LABEL_UNRESOLVED,
            reason=(
                "the only evidence bearing on local support is variance-unstable, so the "
                "local label cannot be negative"
            ),
            **counts,
        )
    if len(correlation) > 0 and len(direction_classes) < int(min_direction_classes):
        return LocalSupport(
            label=LABEL_UNRESOLVED,
            reason=(
                f"correlation-following confirmations span only {len(direction_classes)} "
                f"direction class(es), fewer than the required {int(min_direction_classes)}; "
                "the design is directionally inadequate rather than negative"
            ),
            **counts,
        )
    if feasible_direction_coverage is not None and float(feasible_direction_coverage) < 1.0:
        return LocalSupport(
            label=LABEL_UNRESOLVED,
            reason=(
                f"only {float(feasible_direction_coverage):.3f} of the declared directions were "
                "feasible at this point (one-sided neighbourhood at a bound); a one-sided "
                "neighbourhood is not isolation"
            ),
            **counts,
        )
    return LocalSupport(
        label=LABEL_NO_LOCAL_SUPPORT,
        reason=(
            f"the complete declared design ({len(evidence)} unique probes) found no qualifying "
            "support; this is a statement about the declared finite design, not a proof that "
            "the point is isolated"
        ),
        **counts,
    )


# ==========================================================================
# The frozen run plan
# ==========================================================================

@dataclass(frozen=True)
class FrozenPlan:
    """A fully signed, validated run plan.

    Constructing this object is the gate: :meth:`load` refuses to return while
    any required decision is unsigned, so every downstream stage can assume its
    numbers were decided before any simulation ran.
    """

    raw: dict
    plan_path: str
    plan_sha256: str
    targets: TargetContract
    seeds: SeedRegistry
    cutoff: float
    discrepancy: float

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        root: Path = ROOT,
        require_signature: bool = True,
    ) -> "FrozenPlan":
        plan_path = Path(path)
        if not plan_path.is_absolute():
            plan_path = root / plan_path
        if not plan_path.exists():
            raise ContractError(f"run plan does not exist: {path}")
        raw = json.loads(plan_path.read_text())
        if not isinstance(raw, Mapping):
            raise ContractError("run plan must be a JSON object")

        # Fail closed BEFORE anything else is interpreted.
        assert_fully_signed(raw)
        if require_signature:
            signature_block(raw)

        targets = TargetContract.from_plan(raw, root=root)
        seeds = SeedRegistry.from_plan(raw)

        acceptance = raw.get("acceptance") or {}
        cutoff = float(acceptance.get("cutoff", ccs.IMPLAUSIBILITY_CUTOFF))
        if cutoff != ccs.IMPLAUSIBILITY_CUTOFF:
            raise ContractError(
                f"this stage may not change the primary acceptance cutoff: plan says {cutoff}, "
                f"the frozen rule is {ccs.IMPLAUSIBILITY_CUTOFF}"
            )
        discrepancy = float(acceptance.get("discrepancy", ccs.PRIMARY_DISCREPANCY))

        plan = cls(
            raw=dict(raw),
            plan_path=str(plan_path),
            plan_sha256=sha256_file(plan_path),
            targets=targets,
            seeds=seeds,
            cutoff=cutoff,
            discrepancy=discrepancy,
        )
        plan.validate()
        return plan

    def get(self, path: str, default: Any = None) -> Any:
        found, value = _dig(self.raw, path)
        return value if found else default

    def require(self, path: str) -> Any:
        found, value = _dig(self.raw, path)
        if not found:
            raise ContractError(f"run plan is missing required field {path!r}")
        if is_unsigned(value):
            raise UnsignedConfigError([UnsignedValue(path, REQUIRED_DECISIONS.get(path, path))])
        return value

    @property
    def k_discovery(self) -> int:
        return self.seeds.k_discovery

    @property
    def k_confirmation(self) -> int:
        return self.seeds.k_confirmation

    def validate(self) -> None:
        """Cross-field checks that a per-field signature cannot catch."""
        p_min = float(self.require("p_parameter.min"))
        p_max = float(self.require("p_parameter.max"))
        if p_min != 0.0:
            raise ContractError(
                f"the p range must start at exact 0 so that M1 nests M0; got {p_min!r}"
            )
        if not (p_max > p_min):
            raise ContractError(f"empty p range [{p_min}, {p_max}]")
        rho = float(self.require("p_parameter.routine_hazard_scale_identity_value"))
        if rho != 1.0:
            raise ContractError(
                "the engine's p=0 identity dispatch requires routine_hazard_scale == 1.0; "
                f"the plan pins {rho!r}, under which M1 would not nest M0"
            )
        epsilon = float(self.require("p_parameter.identity_epsilon"))
        if epsilon < 0.0:
            raise ContractError(f"p_parameter.identity_epsilon must be non-negative; got {epsilon}")
        if epsilon >= p_max:
            raise ContractError(
                f"p_parameter.identity_epsilon ({epsilon}) covers the whole declared p range "
                f"[{p_min}, {p_max}]; no p > 0 probe could ever be drawn"
            )

        bins = [float(b) for b in self.require("p_parameter.bins")]
        if sorted(bins) != bins:
            raise ContractError(f"p coverage bins must be ascending: {bins}")
        if bins[0] < p_min or bins[-1] > p_max:
            raise ContractError(
                f"p coverage bins {bins} fall outside the declared range [{p_min}, {p_max}]"
            )

        scales = [float(s) for s in self.require("ambient.scales")]
        if len(scales) < 2:
            raise ContractError(f"the spec requires at least two ambient scales; got {scales}")
        if sorted(scales) != scales or len(set(scales)) != len(scales):
            raise ContractError(f"ambient scales must be ascending and distinct: {scales}")
        if any(s <= 0.0 for s in scales):
            raise ContractError(f"ambient scales must be positive: {scales}")

        semantics = str(self.require("ambient.scale_semantics"))
        if semantics not in ("radius", "coordinate_bound"):
            raise ContractError(
                "ambient.scale_semantics must be 'radius' or 'coordinate_bound' "
                f"(the spec forbids inferring it); got {semantics!r}"
            )

        fractions = [float(f) for f in self.require("graph.bridge_fractions")]
        if sorted(fractions) != fractions or len(set(fractions)) != len(fractions):
            raise ContractError(f"bridge fractions must be ascending and distinct: {fractions}")
        if any(not (0.0 < f < 1.0) for f in fractions):
            raise ContractError(f"bridge fractions must lie strictly in (0, 1): {fractions}")

        alpha = float(self.require("promotion.variance_alpha"))
        if not (0.0 < alpha < 1.0):
            raise ContractError(f"promotion.variance_alpha must be in (0, 1); got {alpha}")

        if int(self.require("support.min_confirming_neighbours")) < 1:
            raise ContractError("support.min_confirming_neighbours must be >= 1")
        if int(self.require("separation.min_direction_classes")) < 1:
            raise ContractError("separation.min_direction_classes must be >= 1")

        min_an = float(self.require("separation.min_anchor_neighbour"))
        if min_an <= 0.0:
            raise ContractError("separation.min_anchor_neighbour must be positive")
        if min_an >= min(scales):
            raise ContractError(
                f"minimum anchor-neighbour separation ({min_an}) is not smaller than the "
                f"smallest ambient scale ({min(scales)}); no probe could ever qualify"
            )

        if int(self.require("budget.max_trajectories")) <= 0:
            raise ContractError("budget.max_trajectories must be positive")
        if int(self.require("budget.max_attempts")) <= 0:
            raise ContractError("budget.max_attempts must be positive")

        quotas = self.kernel_quotas()
        if not quotas:
            raise ContractError("no kernel quotas are declared")

    def kernel_quotas(self) -> dict[str, int]:
        """Declared per-anchor proposal quota by kernel family.

        Checked before dispatch and again after merge (spec sec. 3).  A kernel
        that exhausts its attempts fails preparation; it may never silently
        fall back to another kernel or pad its quota with duplicates.
        """
        scales = [float(s) for s in self.require("ambient.scales")]
        per_scale = int(self.require("ambient.quota_per_scale"))
        return {
            KERNEL_AMBIENT: per_scale * len(scales),
            KERNEL_ARCHIVE_FOLLOWING: int(self.require("archive_following.quota")),
            KERNEL_CROSS_BLOCK: int(self.require("cross_block.quota")),
        }

    def ambient_scales(self) -> tuple[float, ...]:
        return tuple(float(s) for s in self.require("ambient.scales"))

    def smaller_ambient_scale(self) -> float:
        return min(self.ambient_scales())

    def bridge_fractions(self) -> tuple[float, ...]:
        return tuple(float(f) for f in self.require("graph.bridge_fractions"))

    def manifest_contract(self) -> dict:
        """The frozen numerical/identity contract, for the stage manifest."""
        return {
            "contract_version": CONTRACT_VERSION,
            "plan_path": self.plan_path,
            "plan_sha256": self.plan_sha256,
            "sign_off": json_safe(self.raw.get("sign_off")),
            "canonical_float_serialisation": "repr(float), -0.0 normalised to 0.0",
            "vector_hash": "sha256(contract_version + '\\n' + 'name=repr(value)' joined by '|')",
            "target_contract_hash": self.targets.hash(),
            "target_order": list(self.targets.frozen_order),
            "n_targets": self.targets.n,
            "normalisation_metric": "u = (x - lower) / (upper - lower) on original bounds",
            "distance_metric": str(self.get("graph.distance_metric", "euclidean_normalised")),
            "distance_metric_role": (
                "edge proposal only; never establishes a region and never asserts "
                "continuous connectivity"
            ),
            "acceptance_cutoff": self.cutoff,
            "discrepancy": self.discrepancy,
            "process_variance": "s_i^2 (1 + 1/K)",
            "seeds": self.seeds.as_dict(),
            "kernel_quotas": self.kernel_quotas(),
            "tolerances": json_safe(self.get("tolerances", {})),
            "limitations": [
                "finite support-detection design; not a recovery of the NROY topology",
                "'no local support detected' is a statement about the declared design",
                "'sampled-path supported' is discrete evidence, not continuous connectivity",
                "local-support labels are forecast-ensemble eligibility, not acceptance",
            ],
        }


__all__ = [name for name in dir() if not name.startswith("_")]
