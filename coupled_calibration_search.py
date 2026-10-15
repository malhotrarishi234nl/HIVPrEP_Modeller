#!/usr/bin/env python3
"""Pure logic for the bounded coupled calibration search (design 2026-09-21).

This module holds the frozen design constants, the parameter/block contract, the
proposal generator, the archive, the SEARCH objective and the ACCEPTANCE rule.
It constructs no simulation engine, runs no simulation and writes no file.
``run_coupled_calibration.py`` supplies the production runner; this module is
imported by that runner, by the tests and by the read-only reviewer.

Scientific contract, stated once here and enforced throughout:

* The search ranks candidates by a SEARCH-ONLY statistic ``J`` built from signed
  residuals standardised by a FROZEN non-process variance ``B_i``.  Increasing a
  candidate's replicate variance cannot improve ``J``.
* ACCEPTANCE is the frozen 13-target primary maximum-implausibility rule with
  predictive process variance ``s_i^2 (1 + 1/K)``, target-relative discrepancy
  0.1 and cutoff 3, under the repository's own ``<=`` boundary convention.
  ``J`` never replaces it and a low ``J`` is not acceptance.
* The numerical search settings are DECLARED COMPUTATIONAL CHOICES.  They are
  not empirical parameter estimates, not a likelihood, not a posterior and not a
  claim of optimality.  Equal treatment of the four reporting groups is a search
  heuristic only.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent

# ==========================================================================
# Authoritative inputs (section 2 of the handoff).  Paths resolve from ROOT.
# ==========================================================================

#: Written into every manifest this design produces.  The seed preflight uses it to
#: tell THIS experiment's own recorded seeds apart from a foreign collision.
DESIGN_NAME = "coupled_calibration_forecast_windows_20260921"

SOURCE_EXPERIMENT = "outputs/calibration/hm_focused_diagnoses_then_aids_20260916"
BASE_CONFIG_PATH = "configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json"
CALIB_SPACE_PATH = "configs/calib_space_v13_25_unordered_orientation_20260921.json"
TARGET_REGISTER_PATH = "configs/hm_target_register_focused_20260916.json"
WAVE_PLAN_PATH = "configs/hm_wave_plan_forecast_windows_20260921.json"
WARM_START_PATH = "configs/hm_forecast_windows_warm_start_20260921.csv"

#: Hashes recorded by the focused source manifest and re-verified in the
#: 2026-09-18 preparation session.  A mismatch is fatal, never a warning.
EXPECTED_INPUT_SHA256 = {
    "base_config": "c7aaec0f3ae36929ff3266f7305c128952e08714575363436d273e73dbd53187",
    "calib_space": "82970c6d542ea7319ba9cc807ea221bb43f4558afdc5d8abb37e96d649649a12",
    "target_register": "87db68409e1a3e0c6ede762b37fd7f8c64008a1ba289b4965eb7a7be645e7a03",
    "wave_plan": "a75fc8656aad6c25996c96be940c20874948b9bf0e09c4635c85e562cfd877ef",
}

# ==========================================================================
# Targets (section 2).  BOTH series are scored from the first evaluation.
# ==========================================================================

DIAGNOSIS_SERIES = "shm_new_diagnoses_msm"
AIDS_SERIES = "rivm_aids_msm"
DIAGNOSIS_YEARS = (1996, 2000, 2004, 2010, 2015, 2019, 2024)
AIDS_YEARS = (2000, 2005, 2010, 2015, 2019, 2022)
N_TARGETS = 13

#: The frozen target ORDER.  Every residual vector, every score table and every
#: stored matrix uses exactly this order.  An unresolved target is fatal.
FROZEN_TARGET_ORDER = tuple(
    [f"{DIAGNOSIS_SERIES}@{year}" for year in DIAGNOSIS_YEARS]
    + [f"{AIDS_SERIES}@{year}" for year in AIDS_YEARS]
)

#: Four reporting/objective groups (section 4).  Reporting device for the search
#: only: NOT weights, NOT a likelihood, NOT part of acceptance.
OBJECTIVE_GROUPS: dict[str, tuple[str, ...]] = {
    "diagnoses_1996_2015": tuple(
        f"{DIAGNOSIS_SERIES}@{y}" for y in (1996, 2000, 2004, 2010, 2015)
    ),
    "aids_2000_2015": tuple(f"{AIDS_SERIES}@{y}" for y in (2000, 2005, 2010, 2015)),
    "diagnoses_after_2015": tuple(f"{DIAGNOSIS_SERIES}@{y}" for y in (2019, 2024)),
    "aids_after_2015": tuple(f"{AIDS_SERIES}@{y}" for y in (2019, 2022)),
}
GROUP_ORDER = tuple(OBJECTIVE_GROUPS)

# ==========================================================================
# Parameter blocks (section 3).  Exhaustive and disjoint over all 33 names.
# ==========================================================================

BLOCKS: dict[str, tuple[str, ...]] = {
    "initial_burden": (
        "seed_prevalence", "seed_msm_fraction", "p_msm", "seed_msm_chronic_share",
        "seed_msm_aids_share", "seed_non_msm_chronic_share", "seed_non_msm_aids_share",
    ),
    "contacts_mixing": (
        "orient_female", "orient_msm", "epsilon_hetero", "epsilon_msm", "activity_med",
        "activity_high", "contact_low", "contact_med", "contact_high",
    ),
    "diagnosis": (
        "diagnosis_1985", "diagnosis_1990", "diagnosis_1997", "diagnosis_2019",
        "msm_diag_mult_1996", "msm_diag_mult_2004", "msm_diag_mult_2019",
    ),
    "care_progression_survival": (
        "art_init_1996", "art_init_2000", "art_init_2015", "art_init_2020",
        "art_dropout_rate", "chronic_to_aids", "chronic_to_aids_on_art_early",
        "chronic_to_aids_on_art_recent", "aids_mortality", "art_mortality_reduction",
    ),
}
BLOCK_ORDER = tuple(BLOCKS)
#: The six unordered block pairs, in a frozen order, cycled three times per batch.
BLOCK_PAIRS = tuple(itertools.combinations(BLOCK_ORDER, 2))

# ==========================================================================
# Search design (sections 5 and 6).  Declared computational choices.
# ==========================================================================

SOURCE_VECTOR_IDS = (620, 621, 548)
MAX_WARM_VECTORS = 24
INITIAL_ARCHIVE_SIZE = 256
N_BATCHES = 28
BATCH_SIZE = 64
GLOBAL_PER_BATCH = 16
FULL_DIFFERENTIAL_PER_BATCH = 24
BLOCK_DIFFERENTIAL_PER_BATCH = 24
MAX_DISCOVERY_VECTORS = 2048

POOL_BEST_J = 64
POOL_PER_GROUP = 16
POOL_DIVERSITY = 32
POOL_SIZE = POOL_BEST_J + POOL_PER_GROUP * len(GROUP_ORDER) + POOL_DIVERSITY

F_VALUES = (0.5, 0.8)
MAX_ATTEMPTS_PER_SLOT = 200
MAX_RAW_PROPOSALS_PER_BATCH = 100_000
#: Computational duplicate tolerance in normalized coordinates.  Deliberately
#: kept separate from every scientific criterion.
DUPLICATE_TOLERANCE = 1e-10

DISCOVERY_SEEDS = tuple(range(200000, 200005))
CONFIRMATION_SEEDS = tuple(range(200100, 200120))
SMOKE_SEEDS = (200200, 200201, 200202)
PROPOSAL_RNG_SEED = 200300
DISCOVERY_REPLICATES = len(DISCOVERY_SEEDS)
CONFIRMATION_REPLICATES = len(CONFIRMATION_SEEDS)

SIMULATION_START_YEAR = 1975
EXTRACT_START_YEAR = 1975
EXTRACT_END_YEAR = 2025
STOCK_YEARS = (1990, 2000, 2010, 2025)

PRIMARY_DISCREPANCY = 0.1
SENSITIVITY_DISCREPANCY = 0.2
DISCREPANCY_METHOD = "relative_to_target"
IMPLAUSIBILITY_CUTOFF = 3.0
SHORTLIST_PRIMARY_PASS_HEAD = 5
SHORTLIST_SIZE = 10

SMOKE_TRAJECTORIES = 2 * len(SMOKE_SEEDS)
DISCOVERY_TRAJECTORIES = MAX_DISCOVERY_VECTORS * DISCOVERY_REPLICATES
CONFIRMATION_TRAJECTORIES = SHORTLIST_SIZE * CONFIRMATION_REPLICATES
TOTAL_TRAJECTORY_BUDGET = SMOKE_TRAJECTORIES + DISCOVERY_TRAJECTORIES + CONFIRMATION_TRAJECTORIES

DEFAULT_WORKERS = 4
MAX_WORKERS = 8
WALL_TIME_CAP_SECONDS = 6 * 3600

#: Status vocabulary written into every manifest.  Only a completed stage gets
#: its corresponding final report.
STATUS_PREPARED = "PREPARED"
STATUS_SMOKE_PASSED = "SMOKE_PASSED"
STATUS_SEARCH_COMPLETE = "SEARCH_COMPLETE"
STATUS_CONFIRMATION_COMPLETE = "CONFIRMATION_COMPLETE"
STATUS_PARTIAL_TIME_LIMIT = "PARTIAL_TIME_LIMIT"
STATUS_FAILED = "FAILED"
ALL_STATUSES = (
    STATUS_PREPARED, STATUS_SMOKE_PASSED, STATUS_SEARCH_COMPLETE,
    STATUS_CONFIRMATION_COMPLETE, STATUS_PARTIAL_TIME_LIMIT, STATUS_FAILED,
)


class ContractError(RuntimeError):
    """A scientific-contract or integrity violation.  Never silently recovered."""


# ==========================================================================
# Small helpers
# ==========================================================================

def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def vector_identity(params: Mapping[str, float]) -> str:
    """Exact cache identity of one parameter vector.

    Built from the full-precision repr of every coordinate, so two vectors share
    an identity only when every coordinate is bit-for-bit equal.  This is an
    EXACT key for caching and indexing; it is deliberately separate from the
    1e-10 max-coordinate diversity tolerance, which is a computational
    near-duplicate rule and not a scientific criterion.
    """
    payload = ";".join(f"{name}={float(params[name])!r}" for name in sorted(params))
    return hashlib.sha256(payload.encode()).hexdigest()


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return [json_safe(v) for v in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, Path):
        return str(value)
    return value


# ==========================================================================
# Parameter space
# ==========================================================================

@dataclass(frozen=True)
class ParameterSpace:
    """The 33 original linear coordinates, their bounds and the block partition.

    No dimension reduction and no log transformation: u = (x - lower)/(upper - lower)
    on the ORIGINAL bounds, exactly as declared in the calibration space file.
    """

    names: tuple[str, ...]
    lower: np.ndarray
    upper: np.ndarray
    parameters: tuple[dict, ...]
    config_paths: tuple[str, ...]

    @classmethod
    def from_calib_space(cls, calib_space: Mapping) -> "ParameterSpace":
        parameters = tuple(dict(p) for p in calib_space["parameters"])
        names = tuple(str(p["name"]) for p in parameters)
        lower = np.array([float(p["min"]) for p in parameters], dtype=float)
        upper = np.array([float(p["max"]) for p in parameters], dtype=float)
        if len(set(names)) != len(names):
            raise ContractError("calibration space has duplicate parameter names")
        if not np.all(np.isfinite(lower)) or not np.all(np.isfinite(upper)):
            raise ContractError("calibration space has non-finite bounds")
        if np.any(upper <= lower):
            bad = [names[i] for i in np.flatnonzero(upper <= lower)]
            raise ContractError(f"calibration space has empty bounds for: {bad}")
        space = cls(names, lower, upper, parameters,
                    tuple(str(p["config_path"]) for p in parameters))
        space.assert_block_partition()
        return space

    @property
    def n(self) -> int:
        return len(self.names)

    def assert_block_partition(self) -> None:
        """The four blocks must be exhaustive, disjoint and equal to the space."""
        flat: list[str] = []
        for block in BLOCK_ORDER:
            flat.extend(BLOCKS[block])
        if len(flat) != len(set(flat)):
            duplicated = sorted({n for n in flat if flat.count(n) > 1})
            raise ContractError(f"block partition repeats parameters: {duplicated}")
        if set(flat) != set(self.names):
            missing = sorted(set(self.names) - set(flat))
            extra = sorted(set(flat) - set(self.names))
            raise ContractError(
                f"block partition does not equal the space: missing={missing}, unknown={extra}"
            )

    def block_indices(self, block: str) -> np.ndarray:
        index = {name: i for i, name in enumerate(self.names)}
        return np.array([index[name] for name in BLOCKS[block]], dtype=int)

    def normalise(self, x: Sequence[float] | Mapping[str, float]) -> np.ndarray:
        vector = self.as_vector(x)
        return (vector - self.lower) / (self.upper - self.lower)

    def denormalise(self, u: Sequence[float]) -> np.ndarray:
        return self.lower + np.asarray(u, dtype=float) * (self.upper - self.lower)

    def as_vector(self, x: Sequence[float] | Mapping[str, float]) -> np.ndarray:
        if isinstance(x, Mapping):
            missing = [name for name in self.names if name not in x]
            if missing:
                raise ContractError(f"parameter vector is missing: {missing}")
            return np.array([float(x[name]) for name in self.names], dtype=float)
        vector = np.asarray(x, dtype=float)
        if vector.shape != (self.n,):
            raise ContractError(f"parameter vector has shape {vector.shape}, expected ({self.n},)")
        return vector

    def as_dict(self, x: Sequence[float]) -> dict[str, float]:
        return {name: float(value) for name, value in zip(self.names, self.as_vector(x))}

    def bounds_table(self) -> pd.DataFrame:
        """The bounds/config-path table generated from the authoritative JSON."""
        block_of = {name: block for block in BLOCK_ORDER for name in BLOCKS[block]}
        return pd.DataFrame([
            {
                "index": i,
                "name": name,
                "block": block_of[name],
                "lower": float(self.lower[i]),
                "upper": float(self.upper[i]),
                "range": float(self.upper[i] - self.lower[i]),
                "config_path": self.config_paths[i],
                "unit": self.parameters[i].get("unit", ""),
            }
            for i, name in enumerate(self.names)
        ])


# ==========================================================================
# Validity: derived parameters, declared constraints, bounds, injection
# ==========================================================================

def derived_frame(space: ParameterSpace, vectors: np.ndarray) -> pd.DataFrame:
    """Apply the repository's own derived-parameter step before any mask."""
    from run_lhs_calibration import _with_derived_parameters  # noqa: PLC0415

    matrix = np.atleast_2d(np.asarray(vectors, dtype=float))
    frame = pd.DataFrame(matrix, columns=list(space.names))
    return _with_derived_parameters(frame)


def constraint_mask(space: ParameterSpace, vectors: np.ndarray) -> np.ndarray:
    """``_valid_draw_mask`` applied AFTER ``_with_derived_parameters``."""
    from run_lhs_calibration import _valid_draw_mask  # noqa: PLC0415

    return _valid_draw_mask(derived_frame(space, vectors)).to_numpy(dtype=bool)


def invalid_reasons(
    space: ParameterSpace,
    vector: Sequence[float],
    *,
    injection_check: Callable[[dict[str, float]], None] | None = None,
) -> list[str]:
    """Every reason this vector is invalid.  Never clips, repairs or sorts.

    Order: finiteness, original bounds, derived+declared constraints, then the
    production injection validation (which enforces the fixed and derived
    constraints the partial draw mask does not cover).
    """
    reasons: list[str] = []
    x = np.asarray(vector, dtype=float)
    if x.shape != (space.n,):
        return [f"wrong dimension {x.shape}, expected ({space.n},)"]
    if not np.all(np.isfinite(x)):
        bad = [space.names[i] for i in np.flatnonzero(~np.isfinite(x))]
        reasons.append(f"non-finite values: {bad}")
        return reasons
    below = np.flatnonzero(x < space.lower)
    above = np.flatnonzero(x > space.upper)
    for i in below:
        reasons.append(f"{space.names[i]}={x[i]!r} below lower bound {space.lower[i]!r}")
    for i in above:
        reasons.append(f"{space.names[i]}={x[i]!r} above upper bound {space.upper[i]!r}")
    if reasons:
        return reasons
    frame = derived_frame(space, x)
    if not bool(constraint_mask(space, x)[0]):
        derived = {c: float(frame.iloc[0][c]) for c in frame.columns if c not in space.names}
        reasons.append(f"violates declared draw constraints; derived={derived}")
    if not reasons and injection_check is not None:
        try:
            injection_check(space.as_dict(x))
        except Exception as exc:  # noqa: BLE001 - the reason is data, not a crash
            reasons.append(f"production injection rejected the vector: {type(exc).__name__}: {exc}")
    return reasons


def is_valid(space, vector, *, injection_check=None) -> bool:
    return not invalid_reasons(space, vector, injection_check=injection_check)


# ==========================================================================
# Frozen targets and the frozen non-process variance B_i
# ==========================================================================

@dataclass(frozen=True)
class TargetSpec:
    """One frozen scalar target with its frozen non-process variance."""

    key: str
    series_name: str
    model_output: str
    year: int
    column: str
    variance_column: str
    z: float
    v_obs_variance: float
    v_obs_status: str
    v_code_variance: float
    v_code_status: str
    v_disc_variance: float
    v_disc_status: str
    extra_variance: float
    b_variance: float
    group: str

    def as_row(self) -> dict:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}


def group_of_target(key: str) -> str:
    for group, keys in OBJECTIVE_GROUPS.items():
        if key in keys:
            return group
    raise ContractError(f"target {key} belongs to no reporting group")


def load_frozen_targets(
    register_path: str | Path,
    wave_plan: Mapping,
    *,
    workbook_path: str | Path | None = None,
    discrepancy: float = PRIMARY_DISCREPANCY,
) -> list[TargetSpec]:
    """Resolve the declared targets live and freeze B_i through existing budget code.

    ``B_i`` is EVERY non-process term of the existing primary budget: the
    observation term as the register resolves it from the live workbook, the
    repository's own V_code term, the target-relative discrepancy term at the
    declared multiplier, and any extra term the budget assembles.  Nothing here
    assumes an observation or code variance of zero; both are read back from the
    constructed budget and recorded.
    """
    import hm_target_register as tr  # noqa: PLC0415
    import hm_variance_budget as vb  # noqa: PLC0415

    register = tr.load_register(register_path, workbook_path=workbook_path)
    scored_years = wave_plan["scored_years"]
    declared = {DIAGNOSIS_SERIES: list(DIAGNOSIS_YEARS), AIDS_SERIES: list(AIDS_YEARS)}
    if {k: [int(y) for y in v] for k, v in scored_years.items()} != declared:
        raise ContractError(
            f"wave plan scored_years {scored_years} differ from the declared selection {declared}"
        )
    selected = register.restrict_years(
        [t for t in register.calibration_targets() if t.series_name in scored_years], scored_years
    )
    resolved_keys = [t.key for t in selected]
    if len(selected) != N_TARGETS:
        raise ContractError(
            f"expected exactly {N_TARGETS} targets, resolved {len(selected)}: {resolved_keys}"
        )
    missing = [k for k in FROZEN_TARGET_ORDER if k not in resolved_keys]
    if missing:
        raise ContractError(f"unresolved targets (never silently dropped): {missing}")
    by_key = {t.key: t for t in selected}

    # Budget construction through the repository's own assembler.  The
    # target-relative discrepancy does not depend on the ensemble, so a
    # placeholder output array is supplied only to satisfy the signature; it
    # cannot influence any term under this method.
    placeholder = {key: np.array([0.0, 1.0]) for key in by_key}
    budgets = tr.build_budgets(
        [by_key[k] for k in FROZEN_TARGET_ORDER], placeholder,
        defaults=register.meta["defaults_effective"],
        v_ensemble=None, disc_multiplier=float(discrepancy), disc_method=DISCREPANCY_METHOD,
    )
    specs: list[TargetSpec] = []
    for key in FROZEN_TARGET_ORDER:
        target, budget = by_key[key], budgets[key]
        if budget.v_ensemble.variance != 0.0:
            raise ContractError(
                f"{key}: the frozen budget must carry no process variance, got "
                f"{budget.v_ensemble.variance}"
            )
        extra = float(sum(term.variance for term in budget.extra_terms))
        b_variance = float(budget.total_variance - budget.v_ensemble.variance)
        if not math.isfinite(b_variance) or b_variance <= 0.0:
            raise ContractError(
                f"{key}: frozen non-process variance must be finite and positive, got {b_variance}"
            )
        recomputed = (
            float(budget.v_obs.variance) + float(budget.v_code.variance)
            + float(budget.v_disc.variance) + extra
        )
        if not math.isclose(recomputed, b_variance, rel_tol=1e-12, abs_tol=0.0):
            raise ContractError(f"{key}: budget terms do not sum to the frozen variance")
        specs.append(TargetSpec(
            key=key, series_name=target.series_name, model_output=target.model_output,
            year=int(target.year), column=f"{target.model_output}_{target.year}",
            variance_column=f"mcvar__{target.model_output}_{target.year}",
            z=float(target.z),
            v_obs_variance=float(budget.v_obs.variance), v_obs_status=str(budget.v_obs.status),
            v_code_variance=float(budget.v_code.variance), v_code_status=str(budget.v_code.status),
            v_disc_variance=float(budget.v_disc.variance), v_disc_status=str(budget.v_disc.status),
            extra_variance=extra, b_variance=b_variance, group=group_of_target(key),
        ))
    grouped = {g: 0 for g in GROUP_ORDER}
    for spec in specs:
        grouped[spec.group] += 1
    expected_grouped = {group: len(OBJECTIVE_GROUPS[group]) for group in GROUP_ORDER}
    if grouped != expected_grouped:
        raise ContractError(f"reporting-group sizes are wrong: {grouped}")
    return specs


def target_arrays(targets: Sequence[TargetSpec]) -> tuple[np.ndarray, np.ndarray]:
    z = np.array([t.z for t in targets], dtype=float)
    b = np.array([t.b_variance for t in targets], dtype=float)
    if not np.all(np.isfinite(b)) or np.any(b <= 0.0):
        raise ContractError("frozen non-process variances must all be finite and positive")
    return z, b


# ==========================================================================
# SEARCH statistic (section 4).  Never an acceptance rule.
# ==========================================================================

def search_residuals(means: Sequence[float], targets: Sequence[TargetSpec]) -> np.ndarray:
    """r_i = (mu_i - z_i)/sqrt(B_i).  Positive means overprediction.

    The denominator deliberately OMITS the candidate-dependent process variance,
    so a noisier candidate cannot improve its own search rank.
    """
    mu = np.asarray(means, dtype=float)
    if mu.shape != (len(targets),):
        raise ContractError(f"expected {len(targets)} means, got {mu.shape}")
    z, b = target_arrays(targets)
    if not np.all(np.isfinite(mu)):
        raise ContractError("non-finite simulator mean; a failed evaluation is not a result")
    return (mu - z) / np.sqrt(b)


def group_rms(residuals: Sequence[float], targets: Sequence[TargetSpec]) -> dict[str, float]:
    """R_G = sqrt(mean(r_i^2)) within each reporting group."""
    r = np.asarray(residuals, dtype=float)
    index = {t.key: i for i, t in enumerate(targets)}
    out: dict[str, float] = {}
    for group in GROUP_ORDER:
        idx = [index[key] for key in OBJECTIVE_GROUPS[group]]
        out[group] = float(np.sqrt(np.mean(r[idx] ** 2)))
    return out


def objective(group_values: Mapping[str, float]) -> tuple[float, float]:
    """(J, mean_R).  J = max_G R_G; mean_R breaks ties before candidate ID."""
    values = [float(group_values[g]) for g in GROUP_ORDER]
    if not all(math.isfinite(v) for v in values):
        raise ContractError(f"non-finite group statistic: {group_values}")
    return float(max(values)), float(np.mean(values))


def ranking_key(record: "CandidateRecord") -> tuple[float, float, int]:
    return (record.j, record.mean_r, record.candidate_id)


# ==========================================================================
# ACCEPTANCE (section 4).  The existing primary maximum rule, unchanged.
# ==========================================================================

@dataclass(frozen=True)
class AcceptanceResult:
    discrepancy: float
    replicates: int
    implausibility: tuple[float, ...]
    total_variance: tuple[float, ...]
    i_max: float
    binding_target: str
    passed: bool
    failed_targets: tuple[str, ...]

    def as_dict(self) -> dict:
        return json_safe({f: getattr(self, f) for f in self.__dataclass_fields__})


def predictive_variance(sample_variances: Sequence[float], replicates: int) -> np.ndarray:
    """s_i^2 (1 + 1/K) -- the existing predictive_local process variance."""
    k = int(replicates)
    if k < 2:
        raise ContractError("predictive process variance needs at least two replicates")
    s2 = np.asarray(sample_variances, dtype=float)
    if not np.all(np.isfinite(s2)) or np.any(s2 < 0.0):
        raise ContractError("sample variances must be finite and non-negative")
    return s2 * (1.0 + 1.0 / k)


def acceptance(
    means: Sequence[float],
    sample_variances: Sequence[float],
    targets: Sequence[TargetSpec],
    *,
    replicates: int,
    discrepancy: float = PRIMARY_DISCREPANCY,
    cutoff: float = IMPLAUSIBILITY_CUTOFF,
) -> AcceptanceResult:
    """Maximum-implausibility acceptance; ``<= cutoff`` passes, as in the repository.

    ``discrepancy`` rescales ONLY the target-relative discrepancy term, exactly
    as the existing sensitivity does.  A sensitivity pass is never a promotion.
    """
    z, b_primary = target_arrays(targets)
    scale = (float(discrepancy) / PRIMARY_DISCREPANCY) ** 2
    disc = np.array([t.v_disc_variance for t in targets], dtype=float)
    b = b_primary - disc + disc * scale
    if not np.all(np.isfinite(b)) or np.any(b <= 0.0):
        raise ContractError("non-positive or non-finite non-process variance under this discrepancy")
    mu = np.asarray(means, dtype=float)
    if mu.shape != (len(targets),):
        raise ContractError(f"expected {len(targets)} means, got {mu.shape}")
    if not np.all(np.isfinite(mu)):
        raise ContractError("non-finite simulator mean; a candidate we could not score is not a pass")
    total = b + predictive_variance(sample_variances, replicates)
    if np.any(total <= 0.0) or not np.all(np.isfinite(total)):
        raise ContractError("non-positive or non-finite total variance")
    implausibility = np.abs(mu - z) / np.sqrt(total)
    order = int(np.argmax(implausibility))
    failed = tuple(targets[i].key for i in np.flatnonzero(implausibility > float(cutoff)))
    return AcceptanceResult(
        discrepancy=float(discrepancy), replicates=int(replicates),
        implausibility=tuple(float(v) for v in implausibility),
        total_variance=tuple(float(v) for v in total),
        i_max=float(implausibility[order]), binding_target=targets[order].key,
        passed=bool(np.all(implausibility <= float(cutoff))), failed_targets=failed,
    )


def repository_acceptance_check(
    means_by_candidate: Mapping[int, Sequence[float]],
    variances_by_candidate: Mapping[int, Sequence[float]],
    targets: Sequence[TargetSpec],
    *,
    register_path: str | Path,
    replicates: int,
    discrepancy: float = PRIMARY_DISCREPANCY,
    cutoff: float = IMPLAUSIBILITY_CUTOFF,
) -> pd.DataFrame:
    """Independent cross-check: rescore the same candidates through ``run_history_matching``.

    This calls the repository's own ``_score_matrix`` in ``predictive_local`` mode
    on a frame built from the raw replicate means and ddof=1 variances, so the
    harness arithmetic is compared against the production scoring path rather
    than against itself.
    """
    import hm_target_register as tr  # noqa: PLC0415
    import run_history_matching as rhm  # noqa: PLC0415

    candidate_ids = sorted(means_by_candidate)
    rows = []
    for cid in candidate_ids:
        row = {"design_id": int(cid)}
        for i, target in enumerate(targets):
            row[target.column] = float(np.asarray(means_by_candidate[cid], dtype=float)[i])
            row[target.variance_column] = float(
                np.asarray(variances_by_candidate[cid], dtype=float)[i]
            )
        rows.append(row)
    frame = pd.DataFrame(rows)
    register = tr.load_register(register_path)
    selected = [t for t in register.calibration_targets() if t.key in set(FROZEN_TARGET_ORDER)]
    ordered = sorted(selected, key=lambda t: FROZEN_TARGET_ORDER.index(t.key))
    if len(ordered) != N_TARGETS:
        raise ContractError(
            f"repository cross-check could not resolve the frozen {N_TARGETS} targets"
        )
    core = rhm._score_matrix(
        frame, ordered, register, disc_multiplier=float(discrepancy),
        disc_method=DISCREPANCY_METHOD, v_ensemble=None,
        ensemble_mode=rhm.PREDICTIVE_LOCAL, replicates=int(replicates),
    )
    if core["unresolved"]:
        raise ContractError(f"repository cross-check left targets unresolved: {core['unresolved']}")
    matrix, names = core["matrix"], core["names"]
    if list(names) != list(FROZEN_TARGET_ORDER):
        raise ContractError(f"repository cross-check target order changed: {names}")
    import hm_implausibility as hm  # noqa: PLC0415
    combined = hm.combine(matrix, names)
    mask = combined.nroy_mask(cutoff=float(cutoff), rule="max")
    return pd.DataFrame({
        "candidate_id": candidate_ids,
        "repository_i_max": combined.i_max,
        "repository_binding_target": combined.binding_target,
        "repository_primary_pass": mask,
    })


# ==========================================================================
# Archive
# ==========================================================================

@dataclass
class CandidateRecord:
    """One evaluated candidate.  Raw seed-level outputs live beside it on disk."""

    candidate_id: int
    batch: int
    origin: str
    provenance: dict
    params: dict[str, float]
    u: np.ndarray
    params_sha256: str
    replicate_seeds: tuple[int, ...]
    means: np.ndarray
    sample_variances: np.ndarray
    residuals: np.ndarray
    group_values: dict[str, float]
    j: float
    mean_r: float
    max_abs_residual: float
    primary: AcceptanceResult
    sensitivity: AcceptanceResult

    @property
    def primary_pass(self) -> bool:
        return bool(self.primary.passed)

    @property
    def sensitivity_pass(self) -> bool:
        return bool(self.sensitivity.passed)

    def summary_row(self) -> dict:
        row = {
            "candidate_id": self.candidate_id, "batch": self.batch, "origin": self.origin,
            "params_sha256": self.params_sha256, "search_j": self.j, "search_mean_group_rms": self.mean_r,
            "max_abs_search_residual": self.max_abs_residual,
            "discovery_primary_pass": self.primary_pass,
            "discovery_primary_i_max": self.primary.i_max,
            "discovery_primary_binding_target": self.primary.binding_target,
            "discovery_sensitivity_pass": self.sensitivity_pass,
            "discovery_sensitivity_i_max": self.sensitivity.i_max,
        }
        for group in GROUP_ORDER:
            row[f"R_{group}"] = self.group_values[group]
        return row


def build_record(
    *,
    candidate_id: int,
    batch: int,
    origin: str,
    provenance: dict,
    params: Mapping[str, float],
    space: ParameterSpace,
    replicate_seeds: Sequence[int],
    means: Sequence[float],
    sample_variances: Sequence[float],
    targets: Sequence[TargetSpec],
    replicates: int,
) -> CandidateRecord:
    residuals = search_residuals(means, targets)
    values = group_rms(residuals, targets)
    j, mean_r = objective(values)
    return CandidateRecord(
        candidate_id=int(candidate_id), batch=int(batch), origin=str(origin),
        provenance=dict(provenance), params={k: float(v) for k, v in params.items()},
        u=space.normalise(params), params_sha256=vector_identity(params),
        replicate_seeds=tuple(int(s) for s in replicate_seeds),
        means=np.asarray(means, dtype=float), sample_variances=np.asarray(sample_variances, dtype=float),
        residuals=residuals, group_values=values, j=j, mean_r=mean_r,
        max_abs_residual=float(np.max(np.abs(residuals))),
        primary=acceptance(means, sample_variances, targets, replicates=replicates,
                           discrepancy=PRIMARY_DISCREPANCY),
        sensitivity=acceptance(means, sample_variances, targets, replicates=replicates,
                               discrepancy=SENSITIVITY_DISCREPANCY),
    )


class Archive:
    """Every evaluated candidate, plus every normalized vector already spent.

    ``known`` holds proposals that consumed budget even when their result was
    lost, so a resumed run can neither re-propose nor re-run them.
    """

    def __init__(self, space: ParameterSpace):
        self.space = space
        self.records: dict[int, CandidateRecord] = {}
        self._known: list[np.ndarray] = []
        self._known_ids: list[int] = []
        self.identities: dict[str, int] = {}

    def __len__(self) -> int:
        return len(self.records)

    def register_known(self, candidate_id: int, u: np.ndarray,
                       identity: str | None = None) -> None:
        self._known.append(np.asarray(u, dtype=float))
        self._known_ids.append(int(candidate_id))
        if identity is not None:
            if identity in self.identities and self.identities[identity] != int(candidate_id):
                raise ContractError(
                    f"candidate {candidate_id} repeats the exact parameter vector of candidate "
                    f"{self.identities[identity]}"
                )
            self.identities[identity] = int(candidate_id)

    def add(self, record: CandidateRecord) -> None:
        if record.candidate_id in self.records:
            raise ContractError(f"candidate {record.candidate_id} added twice")
        self.records[record.candidate_id] = record

    @property
    def known_matrix(self) -> np.ndarray:
        if not self._known:
            return np.empty((0, self.space.n), dtype=float)
        return np.vstack(self._known)

    def is_duplicate(self, u: Sequence[float], extra: Sequence[np.ndarray] = ()) -> bool:
        """Computational duplicate: max-coordinate distance below the tolerance."""
        candidate = np.asarray(u, dtype=float)
        for matrix in (self.known_matrix, np.atleast_2d(np.vstack(extra)) if len(extra) else None):
            if matrix is None or matrix.size == 0:
                continue
            if np.any(np.max(np.abs(matrix - candidate), axis=1) <= DUPLICATE_TOLERANCE):
                return True
        return False

    def ordered(self) -> list[CandidateRecord]:
        """Deterministic order: by candidate ID, never by completion order."""
        return [self.records[cid] for cid in sorted(self.records)]

    def ranked(self) -> list[CandidateRecord]:
        return sorted(self.ordered(), key=ranking_key)


# ==========================================================================
# Parent pool (section 5)
# ==========================================================================

@dataclass
class PoolSelection:
    ids: list[int]
    provenance: dict[int, str]

    def as_rows(self) -> list[dict]:
        return [{"candidate_id": cid, "selected_as": self.provenance[cid]} for cid in self.ids]


def select_parent_pool(
    records: Sequence[CandidateRecord],
    *,
    n_best: int = POOL_BEST_J,
    n_per_group: int = POOL_PER_GROUP,
    n_diversity: int = POOL_DIVERSITY,
) -> PoolSelection:
    """64 proposal parents drawn from the ENTIRE completed archive.

    Group specialists are proposal parents only; a good R_G is not acceptance and
    not a claim that the candidate fits jointly.  Every archived vector stays
    available to every later selection.
    """
    if not records:
        raise ContractError("cannot build a parent pool from an empty archive")
    ordered = sorted(records, key=ranking_key)
    chosen: list[int] = []
    provenance: dict[int, str] = {}

    def take(candidates: Iterable[CandidateRecord], limit: int, label: str) -> None:
        taken = 0
        for record in candidates:
            if taken >= limit:
                return
            if record.candidate_id in provenance:
                continue
            chosen.append(record.candidate_id)
            provenance[record.candidate_id] = label
            taken += 1

    take(ordered, n_best, "best_by_J")
    for group in GROUP_ORDER:
        ranked = sorted(records, key=lambda r: (r.group_values[group], r.candidate_id))
        take(ranked, n_per_group, f"best_by_R_{group}")

    by_id = {r.candidate_id: r for r in records}
    remaining = [cid for cid in sorted(by_id) if cid not in provenance]
    for _ in range(min(n_diversity, len(remaining))):
        selected_matrix = np.vstack([by_id[cid].u for cid in chosen])
        best_id, best_distance = None, -np.inf
        for cid in remaining:
            distance = float(np.min(np.linalg.norm(selected_matrix - by_id[cid].u, axis=1)))
            if distance > best_distance:  # ties keep the smaller ID: `remaining` is sorted
                best_id, best_distance = cid, distance
        chosen.append(best_id)
        provenance[best_id] = "maximin_diversity"
        remaining.remove(best_id)
    return PoolSelection(ids=chosen, provenance=provenance)


# ==========================================================================
# Proposals (section 5)
# ==========================================================================

@dataclass
class Proposal:
    candidate_id: int
    batch: int
    origin: str
    params: dict[str, float]
    u: np.ndarray
    provenance: dict

    @property
    def params_sha256(self) -> str:
        return vector_identity(self.params)


class ProposalGenerator:
    """Deterministic, restartable proposal generation in a dedicated RNG stream.

    Simple adaptive differential search over the original box.  NOT an emulator,
    gradient method, posterior sampler or proof of efficient global optimization.
    """

    def __init__(self, space: ParameterSpace, seed: int = PROPOSAL_RNG_SEED):
        self.space = space
        self.rng = np.random.default_rng(int(seed))
        self.seed = int(seed)

    # -- restartable state --------------------------------------------------
    def get_state(self) -> dict:
        return json_safe({"seed": self.seed, "bit_generator": self.rng.bit_generator.state})

    def set_state(self, state: Mapping) -> None:
        if int(state["seed"]) != self.seed:
            raise ContractError("proposal RNG seed changed across resume")
        restored = json.loads(json.dumps(state["bit_generator"]))
        restored["state"]["state"] = int(restored["state"]["state"])
        restored["state"]["inc"] = int(restored["state"]["inc"])
        self.rng.bit_generator.state = restored

    # -- global draws -------------------------------------------------------
    def global_draws(
        self,
        n: int,
        *,
        exclude: Callable[[np.ndarray], bool],
        injection_check=None,
        raw_budget: int = MAX_RAW_PROPOSALS_PER_BATCH,
    ) -> tuple[list[np.ndarray], dict]:
        """Constrained LHS over the ORIGINAL full domain.

        Uses the repository's own ``_uniform_draws``/``_with_derived_parameters``/
        ``_valid_draw_mask`` construction, with explicit raw-proposal accounting
        so the declared generation cap is enforced rather than assumed.
        """
        from run_lhs_calibration import _uniform_draws  # noqa: PLC0415

        accepted: list[np.ndarray] = []
        raw = 0
        rejected = {"constraints": 0, "duplicate": 0, "injection": 0}
        while len(accepted) < n:
            block = max(n * 4, 64)
            if raw + block > raw_budget:
                block = raw_budget - raw
            if block <= 0:
                raise ContractError(
                    f"constraint-generation failure: {raw} raw global proposals produced only "
                    f"{len(accepted)} of {n} valid distinct vectors within the declared cap"
                )
            seed = int(self.rng.integers(0, 2**31 - 1))
            frame = _uniform_draws(self.space.parameters, block, seed)
            raw += block
            matrix = frame[list(self.space.names)].to_numpy(dtype=float)
            mask = constraint_mask(self.space, matrix)
            for i in np.flatnonzero(mask):
                if len(accepted) >= n:
                    break
                vector = matrix[i]
                u = self.space.normalise(vector)
                if exclude(u):
                    rejected["duplicate"] += 1
                    continue
                reasons = invalid_reasons(self.space, vector, injection_check=injection_check)
                if reasons:
                    rejected["injection"] += 1
                    continue
                accepted.append(u)
            rejected["constraints"] += int((~mask).sum())
        return accepted, {"raw_proposals": raw, "rejected": rejected}

    # -- differential moves -------------------------------------------------
    def differential(self, pool_u: np.ndarray, f: float, indices: np.ndarray | None) -> tuple[np.ndarray, dict]:
        """u_new = u_a + F (u_b - u_c) on all coordinates, or on ``indices`` only."""
        a, b, c = (int(i) for i in self.rng.choice(len(pool_u), size=3, replace=False))
        u_new = pool_u[a].copy()
        delta = float(f) * (pool_u[b] - pool_u[c])
        if indices is None:
            u_new = u_new + delta
        else:
            u_new[indices] = u_new[indices] + delta[indices]
        return u_new, {"parent_rows": [a, b, c], "F": float(f)}

    def propose_batch(
        self,
        batch: int,
        pool: PoolSelection,
        archive: Archive,
        *,
        first_candidate_id: int,
        injection_check=None,
    ) -> tuple[list[Proposal], dict]:
        """One synchronous batch: 12 global, 18 full-coordinate, 18 two-block moves."""
        by_id = {r.candidate_id: r for r in archive.records.values()}
        pool_u = np.vstack([by_id[cid].u for cid in pool.ids])
        batch_u: list[np.ndarray] = []

        def taken(u: np.ndarray) -> bool:
            return archive.is_duplicate(u, extra=batch_u)

        proposals: list[Proposal] = []
        diagnostics = {
            "batch": batch, "attempts": 0, "fallbacks": 0,
            "rejected": {"out_of_bounds": 0, "constraints": 0, "duplicate": 0,
                         "unchanged_blocks": 0, "injection": 0},
        }
        globals_u, global_diag = self.global_draws(
            GLOBAL_PER_BATCH, exclude=taken, injection_check=injection_check)
        diagnostics["global"] = global_diag
        for u in globals_u:
            batch_u.append(u)
        next_id = int(first_candidate_id)
        for u in globals_u:
            proposals.append(Proposal(
                candidate_id=next_id, batch=batch, origin="global_lhs",
                params=self.space.as_dict(self.space.denormalise(u)), u=u,
                provenance={"origin": "global_lhs", "domain": "original_full_box"},
            ))
            next_id += 1

        plan: list[tuple[str, tuple[str, ...] | None]] = (
            [("differential_full", None)] * FULL_DIFFERENTIAL_PER_BATCH
            + [("differential_block", BLOCK_PAIRS[i % len(BLOCK_PAIRS)])
               for i in range(BLOCK_DIFFERENTIAL_PER_BATCH)]
        )
        for slot, (origin, blocks) in enumerate(plan):
            f = F_VALUES[slot % len(F_VALUES)]
            indices = None
            if blocks is not None:
                indices = np.concatenate([self.space.block_indices(b) for b in blocks])
            accepted = None
            attempts = 0
            while attempts < MAX_ATTEMPTS_PER_SLOT:
                attempts += 1
                diagnostics["attempts"] += 1
                u_new, info = self.differential(pool_u, f, indices)
                if np.any(u_new < 0.0) or np.any(u_new > 1.0):
                    diagnostics["rejected"]["out_of_bounds"] += 1
                    continue
                if blocks is not None:
                    changed = {
                        block: bool(np.any(np.abs(
                            u_new[self.space.block_indices(block)]
                            - pool_u[info["parent_rows"][0]][self.space.block_indices(block)]
                        ) > 0.0))
                        for block in blocks
                    }
                    if not all(changed.values()):
                        diagnostics["rejected"]["unchanged_blocks"] += 1
                        continue
                if taken(u_new):
                    diagnostics["rejected"]["duplicate"] += 1
                    continue
                vector = self.space.denormalise(u_new)
                reasons = invalid_reasons(self.space, vector, injection_check=injection_check)
                if reasons:
                    diagnostics["rejected"]["constraints"] += 1
                    continue
                accepted = (u_new, {
                    "origin": origin, "F": f, "blocks": list(blocks) if blocks else None,
                    "parents": [pool.ids[i] for i in info["parent_rows"]],
                    "parent_roles": ["a", "b", "c"],
                    "attempts": attempts, "fallback": False,
                })
                break
            if accepted is None:
                diagnostics["fallbacks"] += 1
                fallback_u, fallback_diag = self.global_draws(
                    1, exclude=taken, injection_check=injection_check)
                accepted = (fallback_u[0], {
                    "origin": origin, "F": f, "blocks": list(blocks) if blocks else None,
                    "parents": None, "attempts": attempts, "fallback": True,
                    "fallback_reason": f"no valid distinct differential proposal in "
                                       f"{MAX_ATTEMPTS_PER_SLOT} attempts",
                    "fallback_generation": fallback_diag,
                })
            u_new, provenance = accepted
            batch_u.append(u_new)
            proposals.append(Proposal(
                candidate_id=next_id, batch=batch,
                origin="global_lhs_fallback" if provenance.get("fallback") else origin,
                params=self.space.as_dict(self.space.denormalise(u_new)), u=u_new,
                provenance=provenance,
            ))
            next_id += 1
        if len(proposals) != BATCH_SIZE:
            raise ContractError(f"batch {batch} produced {len(proposals)} proposals, expected {BATCH_SIZE}")
        counts = {origin: sum(1 for p in proposals if p.provenance["origin"] == origin)
                  for origin in ("global_lhs", "differential_full", "differential_block")}
        if counts != {"global_lhs": GLOBAL_PER_BATCH,
                      "differential_full": FULL_DIFFERENTIAL_PER_BATCH,
                      "differential_block": BLOCK_DIFFERENTIAL_PER_BATCH}:
            raise ContractError(f"batch {batch} quota violated: {counts}")
        diagnostics["counts"] = counts
        return proposals, diagnostics


# ==========================================================================
# Initial archive (section 5)
# ==========================================================================

def build_initial_vectors(
    space: ParameterSpace,
    source_frame: pd.DataFrame,
    warm_frame: pd.DataFrame,
    generator: ProposalGenerator,
    *,
    injection_check=None,
    size: int = INITIAL_ARCHIVE_SIZE,
    source_vector_ids: Sequence[int] = SOURCE_VECTOR_IDS,
    source_experiment: str = SOURCE_EXPERIMENT,
    warm_start_path: str = WARM_START_PATH,
    max_warm_vectors: int = MAX_WARM_VECTORS,
) -> tuple[list[dict], dict]:
    """Source vectors, then warm-start rows in FILE ORDER, then constrained LHS.

    Invalid warm vectors are rejected with a recorded reason and never clipped
    into validity.  Old output scores are never reused: every vector here is
    re-evaluated on fresh common random numbers.
    """
    entries: list[dict] = []
    rejections: list[dict] = []
    seen: list[np.ndarray] = []

    def accept(vector: np.ndarray, origin: str, provenance: dict) -> bool:
        u = space.normalise(vector)
        if seen and np.any(np.max(np.abs(np.vstack(seen) - u), axis=1) <= DUPLICATE_TOLERANCE):
            rejections.append({**provenance, "origin": origin, "reason": "duplicate of an earlier initial vector"})
            return False
        reasons = invalid_reasons(space, vector, injection_check=injection_check)
        if reasons:
            rejections.append({**provenance, "origin": origin, "reason": "; ".join(reasons)})
            return False
        seen.append(u)
        params = space.as_dict(vector)
        entries.append({"origin": origin, "provenance": provenance, "params": params, "u": u,
                        "params_sha256": vector_identity(params)})
        return True

    warm_accepted = 0
    for design_id in source_vector_ids:
        if design_id not in source_frame.index:
            raise ContractError(f"source design {design_id} is absent from the source experiment")
        vector = source_frame.loc[design_id, list(space.names)].to_numpy(dtype=float)
        if accept(vector, "warm_source_vector",
                  {"source_design_id": int(design_id), "source": source_experiment,
                   "prior_status_carried_forward": False}):
            warm_accepted += 1

    for row_index in range(len(warm_frame)):
        if warm_accepted >= max_warm_vectors:
            rejections.append({"warm_start_row": row_index, "origin": "warm_start_csv",
                               "reason": f"warm cap of {max_warm_vectors} already reached"})
            continue
        vector = warm_frame.iloc[row_index][list(space.names)].to_numpy(dtype=float)
        if accept(vector, "warm_start_csv",
                  {"warm_start_row": int(row_index), "source": warm_start_path,
                   "prior_status_carried_forward": False}):
            warm_accepted += 1

    n_fill = size - len(entries)
    if n_fill < 0:
        raise ContractError(f"warm vectors ({len(entries)}) already exceed the archive size {size}")

    def taken(u: np.ndarray) -> bool:
        return bool(seen) and bool(np.any(np.max(np.abs(np.vstack(seen) - u), axis=1) <= DUPLICATE_TOLERANCE))

    fill_u, fill_diag = generator.global_draws(n_fill, exclude=taken, injection_check=injection_check)
    for u in fill_u:
        seen.append(u)
        params = space.as_dict(space.denormalise(u))
        entries.append({"origin": "initial_lhs", "provenance": {"domain": "original_full_box"},
                        "params": params, "u": u, "params_sha256": vector_identity(params)})
    if len(entries) != size:
        raise ContractError(f"initial archive has {len(entries)} vectors, expected {size}")
    diagnostics = {
        "n_warm_accepted": warm_accepted, "n_lhs_fill": n_fill,
        "warm_cap": max_warm_vectors, "rejections": rejections, "lhs_generation": fill_diag,
    }
    return entries, diagnostics


# ==========================================================================
# Shortlist (section 7)
# ==========================================================================

def freeze_shortlist(records: Sequence[CandidateRecord], size: int = SHORTLIST_SIZE) -> list[dict]:
    """Up to five discovery primary passes by J, then the remaining lowest J.

    There is no requirement to find ten passers.  Near misses are retained and
    labelled as such: confirmation may reveal that a near miss passes.
    """
    ranked = sorted(records, key=ranking_key)
    shortlist: list[dict] = []
    for record in ranked:
        if len(shortlist) >= min(SHORTLIST_PRIMARY_PASS_HEAD, size):
            break
        if record.primary_pass:
            shortlist.append({"candidate_id": record.candidate_id,
                              "selected_as": "discovery_primary_pass",
                              "search_j": record.j})
    chosen = {entry["candidate_id"] for entry in shortlist}
    for record in ranked:
        if len(shortlist) >= size:
            break
        if record.candidate_id in chosen:
            continue
        shortlist.append({
            "candidate_id": record.candidate_id,
            "selected_as": "lowest_J_near_miss" if not record.primary_pass
            else "lowest_J_additional_primary_pass",
            "search_j": record.j,
        })
        chosen.add(record.candidate_id)
    return shortlist


# ==========================================================================
# Budget accounting (section 6)
# ==========================================================================

@dataclass
class TrajectoryBudget:
    """Launch attempts, not successes, consume the budget."""

    cap: int
    launched: int = 0
    finished: int = 0
    failed: int = 0
    lost: int = 0
    stage_counts: dict = field(default_factory=dict)

    def reserve(self, n: int, stage: str) -> None:
        if self.launched + int(n) > self.cap:
            raise ContractError(
                f"{stage}: reserving {n} trajectories would exceed the declared cap "
                f"({self.launched} already launched of {self.cap})"
            )
        self.launched += int(n)
        self.stage_counts[stage] = self.stage_counts.get(stage, 0) + int(n)

    @property
    def remaining(self) -> int:
        return self.cap - self.launched

    def as_dict(self) -> dict:
        return {"cap": self.cap, "launched": self.launched, "finished": self.finished,
                "failed": self.failed, "lost_in_flight": self.lost,
                "remaining": self.remaining, "by_stage": dict(self.stage_counts)}


def budget_table() -> list[dict]:
    """The declared scheduled-trajectory budget.  Test runs are counted separately."""
    return [
        {"stage": "three paired harness-versus-production smoke checks",
         "maximum_full_trajectories": SMOKE_TRAJECTORIES},
        {"stage": f"{INITIAL_ARCHIVE_SIZE} initial + {N_BATCHES}x{BATCH_SIZE} adaptive vectors, "
                  f"K={DISCOVERY_REPLICATES}",
         "maximum_full_trajectories": DISCOVERY_TRAJECTORIES},
        {"stage": f"at most {SHORTLIST_SIZE} fresh confirmations, K={CONFIRMATION_REPLICATES}",
         "maximum_full_trajectories": CONFIRMATION_TRAJECTORIES},
        {"stage": "total scientific/smoke maximum", "maximum_full_trajectories": TOTAL_TRAJECTORY_BUDGET},
    ]


# ==========================================================================
# Explanatory schedule diagnostics (section 3)
# ==========================================================================

def effective_contact_products(config: Mapping) -> list[dict]:
    """Orientation multiplier x activity contact rate, per orientation and activity.

    Explanatory diagnostic only.  These products are NOT calibration parameters
    and activity classes are neither collapsed nor given extra monotonicity.
    Under the production model they carry no calendar-time schedule, which is
    itself part of what this table reports.
    """
    from config_utils import extract_value  # noqa: PLC0415

    groups = config["transmission_model"]["groups"]
    multipliers = extract_value(groups["advanced"]["orientation_contact_multipliers"])
    activities = extract_value(groups["sexual_activity_groups"])
    rows = []
    for orientation, multiplier in multipliers.items():
        for activity in activities:
            name = extract_value(activity.get("name", activity))
            rate = float(activity["contact_rate"])
            rows.append({
                "orientation": orientation, "activity": name,
                "orientation_multiplier": float(multiplier), "base_contact_rate": rate,
                "effective_annual_contact_product": float(multiplier) * rate,
                "time_varying": False,
            })
    return rows


def msm_diagnosis_probability_table(
    config: Mapping, years: Sequence[int], *, years_per_step: float = 0.25
) -> list[dict]:
    """Clipped annual and per-step MSM diagnosis probabilities, production semantics.

    Reproduces ``run_v3._step_diagnosis`` exactly: the base availability schedule
    (``default_before=0.0``), the multiplier schedule (``default_before=1.0``,
    floored at 0), ``clip(base*multiplier, 0, 1)`` and the clipped step rate.
    """
    from config_utils import extract_value  # noqa: PLC0415
    from schedule_utils import TimeSchedule  # noqa: PLC0415

    diagnosis = config["transmission_model"]["disease"]["diagnosis"]
    base_block = diagnosis["schedule"]
    base = TimeSchedule(
        {str(k): float(v) for k, v in extract_value(base_block["value"]).items()},
        interpolation=extract_value(base_block.get("interpolation", {"value": "stepwise"})),
        default_before=0.0,
    )
    msm_block = diagnosis["msm_relative_diagnosis"]
    if extract_value(msm_block.get("enabled", {"value": False})) is not True:
        raise ContractError("the MSM relative-diagnosis path must be enabled for this design")
    schedule_block = msm_block["schedule"]
    multiplier = TimeSchedule(
        {str(k): float(v) for k, v in extract_value(schedule_block["value"]).items()},
        interpolation=extract_value(schedule_block.get("interpolation", {"value": "stepwise"})),
        default_before=1.0,
    )
    rows = []
    for year in years:
        annual = float(base.get_value(float(year)))
        mult = max(float(multiplier.get_value(float(year))), 0.0)
        msm_annual = float(np.clip(annual * mult, 0.0, 1.0))
        step = float(np.clip(1.0 - (1.0 - msm_annual) ** years_per_step, 0.0, 1.0))
        rows.append({
            "year": int(year), "base_annual_diagnosis_probability": annual,
            "msm_multiplier": mult, "clipped_msm_annual_probability": msm_annual,
            "clipped_msm_step_probability": step,
            "clipped": bool(annual * mult > 1.0),
        })
    return rows
