#!/usr/bin/env python3
"""The M0 / M1-at-``p=0`` identity gate: a pre-production command, not a test.

``ROBUST_NROY_FREEZE_SPEC_2026-10-14.md`` section 2 requires that "an automated
multi-vector, multi-seed identity test must show that this boundary executes
the legacy path and gives identical scientific outputs **before M1 is
launched**".

Why this module exists at all
-----------------------------
The identity checks already existed as pytests.  Every one of them builds a
real ``SimulationEngineV3``, so every one of them is **skipped** under the
repository's no-engine guard::

    .venv/bin/python -m pytest -p pytest_no_engine_guard -rs tests/

That is the correct behaviour for the guard and a dangerous way to hold a
production prerequisite: a green full-suite run says nothing about the
identity, and the first revision of those tests passed for over a day while
three of its deliberate-failure guards were red, because they were only ever
run under the guard.

So the gate is not a test.  It is a command that runs the engine
unconditionally, cannot be skipped by any pytest plugin, and writes a signed
artifact recording *what* it proved and *against which code*.
:func:`assert_gate_satisfied` then refuses an M1 design unless that artifact
exists, passed, and was produced by the code now on disk.

The checks
----------
1. **Identity** -- a genuine no-block M0 configuration against
   ``enabled=true, rho=1.0, p=0`` must give bit-identical ``final_state`` and
   summary, across several complete joint vectors and several seeds.
2. **Non-vacuity** -- the fixture must actually exercise the mechanism:
   infections must occur and the undiagnosed AIDS pool must be non-empty.  An
   identity proved on an epidemic that never started is true for *any* ``p``
   and proves nothing.
3. **Deliberate failure** -- the comparison must fail when a non-``p`` input
   differs, and when the routine hazard scale is unpinned.  A check that cannot
   fail is not a check.
4. **Live mechanism** -- a positive ``p`` must change the trajectory, so the
   identity is a property of the dispatch rather than of an inert parameter.

Every one of the four is required to pass.  A gate that recorded only the
first would be satisfied by the vacuous fixture that this design exists to
prevent.
"""

from __future__ import annotations

import copy
import importlib.metadata
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

import coupled_calibration_search as ccs
import nroy_contract as nc
from coupled_calibration_search import ContractError

ROOT = Path(__file__).resolve().parent

GATE_FILENAME = "m0_m1_identity_gate.json"
GATE_VERSION = "m0_m1_identity_gate/1.0.0"

#: Config path of the stage-structured diagnosis block, in raw JSON shape.
PROFILE_PATH = ("transmission_model", "disease", "diagnosis", "stage_structured_diagnosis")

#: Modules whose content decides whether the identity holds.  The gate records
#: their hash, and an M1 design is refused if they have changed since.
GATE_CODE_MODULES = (
    "nroy_identity_gate.py", "run_v3.py", "stage_structured_diagnosis.py", "counter_based_rng.py",
    "stochastic_allocation.py", "run_lhs_calibration.py", "calib_extractor.py",
    "hm_target_register.py", "nroy_contract.py", "run_robust_nroy_freeze.py",
    "engine_v3.py", "config_utils.py", "config_reader.py",
    "transmission_corrected.py", "demography.py", "migration.py",
    "schedule_utils.py", "aids_observation.py", "exogenous_diagnoses.py",
    "output_writer.py", "hm_variance_budget.py",
)

GateConfigFactory = Callable[[str, float, float, float], dict]

#: Several seeds: the identity must hold per realised trajectory, not merely in
#: distribution.
DEFAULT_SEEDS = (9182, 20260101, 777003)

#: Default base config for the gate run and the end year it is truncated to.
#:
#: 1990 is load-bearing and must not be lowered.  ``1980_test.json`` natively
#: runs to 2025; truncated to 1982 the epidemic never takes off (peak
#: prevalence 0.00%, zero diagnoses) and M0 == M1 holds for *any* ``p``.  At
#: 1990 the run is about a second and produces enough diagnoses for the
#: AIDS-symptom mechanism to bite.  :func:`check_not_vacuous` enforces this
#: rather than trusting the comment.
DEFAULT_BASE_CONFIG = "configs/1980_test.json"
DEFAULT_END_YEAR = 1990

#: Non-vacuity requires both: infections must happen, and there must be an
#: undiagnosed AIDS-stage pool for the symptom hazard to act on.
VACUITY_COLUMNS = ("new_infections_msm", "aids_undiagnosed_start")

#: The certificate is only complete when every one of these named checks is
#: present exactly once.  Keep this next to the artifact contract rather than
#: reconstructing it at the validation call site: the names are part of the
#: signed evidence surface.
REQUIRED_GATE_CHECKS = (
    "not_vacuous",
    "identity_at_exact_p_zero",
    "deliberate_failures_do_fail",
    "positive_p_changes_the_trajectory",
)


def scientific_runtime_fingerprint() -> dict[str, str]:
    """Runtime dependencies capable of changing deterministic trajectories.

    Source hashes alone are not a complete scientific fingerprint: Python,
    NumPy, pandas and SciPy versions (and the execution platform) can change
    RNG, distribution, reduction or floating-point behaviour without editing
    any repository file.
    """
    try:
        scipy_version = importlib.metadata.version("scipy")
    except importlib.metadata.PackageNotFoundError:
        scipy_version = "missing"
    return {
        "python_implementation": platform.python_implementation(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scipy": scipy_version,
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


# ==========================================================================
# Config shaping
# ==========================================================================

def gate_config(
    *,
    base_config: str | Path = DEFAULT_BASE_CONFIG,
    end_year: int = DEFAULT_END_YEAR,
    root: Path = ROOT,
) -> dict:
    """A short **stochastic** run.  Stochastic mode is the case that can break."""
    path = Path(base_config)
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        raise ContractError(f"identity-gate base config does not exist: {path}")
    config = nc.strict_json_load(path, what="identity-gate base config")
    if not isinstance(config, Mapping):
        raise ContractError(f"identity-gate base config must be a JSON object: {path}")
    config["run_control"]["end_year"] = {"value": int(end_year)}
    config["run_control"]["deterministic_mode"] = {"value": False}
    return config


def without_stage_block(config: Mapping) -> dict:
    """A genuine M0 configuration: the block is **absent**, not merely disabled."""
    out = copy.deepcopy(dict(config))
    node: Any = out
    for key in PROFILE_PATH[:-1]:
        node = node[key]
    node.pop(PROFILE_PATH[-1], None)
    return out


def with_stage_block(
    config: Mapping, *, rho: float, p: float, start_year: int = 1985
) -> dict:
    out = copy.deepcopy(dict(config))
    node: Any = out
    for key in PROFILE_PATH[:-1]:
        node = node[key]
    node[PROFILE_PATH[-1]] = {
        "enabled": {"value": True},
        "routine_hazard_scale": {"value": float(rho)},
        "aids_symptom_annual_probability": {"value": float(p)},
        "symptom_start_year": {"value": int(start_year)},
    }
    return out


def perturbed_config(config: Mapping, factor: float) -> dict:
    """Change exactly one non-``p`` input: the MSM contact multiplier."""
    out = copy.deepcopy(dict(config))
    node = out["transmission_model"]["groups"]["advanced"][
        "orientation_contact_multipliers"]["value"]
    node["MSM"] = float(node["MSM"]) * float(factor)
    return out


def run_engine(config: Mapping, seed: int) -> dict:
    """One trajectory.  The only place in this stage that builds an engine."""
    from run_v3 import SimulationEngineV3  # noqa: PLC0415 - engine-only import

    engine = SimulationEngineV3(dict(config), seed=int(seed))
    engine.output_writer.enabled = False
    return engine.run()


def identical(left: Mapping, right: Mapping) -> tuple[bool, str]:
    """Bit-identical final state and summary, or the reason they differ."""
    try:
        np.testing.assert_array_equal(left["final_state"], right["final_state"])
    except AssertionError as exc:
        return False, f"final_state differs: {exc}".splitlines()[0]
    try:
        pd.testing.assert_frame_equal(
            left["summary"], right["summary"], check_exact=True
        )
    except AssertionError as exc:
        return False, f"summary differs: {str(exc).splitlines()[0]}"
    return True, "final_state and summary are bit-identical"


# ==========================================================================
# The four checks
# ==========================================================================

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "check": self.name,
            "passed": bool(self.passed),
            "detail": self.detail,
            "evidence": nc.json_safe(self.evidence),
        }


def check_not_vacuous(config: Mapping, seed: int) -> CheckResult:
    """The mechanism must be able to fire, or the identity proves nothing."""
    summary = run_engine(without_stage_block(config), seed)["summary"]
    missing = [c for c in VACUITY_COLUMNS if c not in summary.columns]
    if missing:
        return CheckResult(
            "not_vacuous", False,
            f"the summary no longer carries {missing}; this gate needs updating against "
            f"the current schema: {list(summary.columns)[:40]}",
        )
    infections = float(summary["new_infections_msm"].sum())
    aids_pool = float(summary["aids_undiagnosed_start"].sum())
    evidence = {"new_infections_msm": infections, "aids_undiagnosed_start": aids_pool}
    if infections <= 0.0:
        return CheckResult(
            "not_vacuous", False,
            f"the fixture produced {infections} MSM infections, so the epidemic never "
            "took off and M0 == M1 holds trivially for any p; raise the end year",
            evidence,
        )
    if aids_pool <= 0.0:
        return CheckResult(
            "not_vacuous", False,
            f"the undiagnosed AIDS-stage pool is {aids_pool}, so the AIDS-symptom hazard "
            "has nothing to act on and the identity is vacuous; raise the end year",
            evidence,
        )
    return CheckResult(
        "not_vacuous", True,
        f"{infections:.0f} MSM infections and an undiagnosed AIDS pool of {aids_pool:.0f}: "
        "the AIDS-symptom mechanism has something to act on",
        evidence,
    )


def check_identity(
    config: Mapping, seeds: Sequence[int], *, vectors: Sequence[float] = (1.0,),
    config_factory: GateConfigFactory | None = None,
) -> CheckResult:
    """Multi-vector, multi-seed exact identity at ``p = 0``.

    ``vectors`` are multipliers applied to one non-``p`` coordinate, so each
    one is a *different complete joint vector* of the shared M0 parameters.
    M0 and M1 are compared at the same vector, which is the only comparison
    the identity claims.
    """
    comparisons: list[dict] = []
    for factor in vectors:
        base = config if factor == 1.0 else perturbed_config(config, factor)
        m0_config = (
            config_factory(nc.MODEL_M0, float(factor), 1.0, 0.0)
            if config_factory else without_stage_block(base)
        )
        m1_config = (
            config_factory(nc.MODEL_M1, float(factor), 1.0, 0.0)
            if config_factory else with_stage_block(base, rho=1.0, p=0.0)
        )
        for seed in seeds:
            m0 = run_engine(m0_config, seed)
            m1 = run_engine(m1_config, seed)
            same, detail = identical(m0, m1)
            comparisons.append({
                "vector_multiplier": float(factor),
                "seed": int(seed),
                "identical": bool(same),
                "detail": detail,
            })
    failures = [c for c in comparisons if not c["identical"]]
    return CheckResult(
        "identity_at_exact_p_zero",
        not failures,
        f"{len(comparisons) - len(failures)}/{len(comparisons)} "
        f"(vector, seed) comparisons bit-identical"
        + ("" if not failures else f"; first failure: {failures[0]}"),
        {"n_comparisons": len(comparisons), "comparisons": comparisons},
    )


def check_deliberate_failures(
    config: Mapping, seed: int, *, config_factory: GateConfigFactory | None = None,
) -> CheckResult:
    """The comparison must fail when it should.  A check that cannot fail is none.

    Two deliberate failures, both required by spec sec. 9: a differing non-``p``
    input, and an unpinned routine hazard scale (``is_identity`` is ``rho == 1.0
    AND p == 0.0``, so ``p = 0`` alone does not execute the legacy path).
    """
    m0 = run_engine(
        config_factory(nc.MODEL_M0, 1.0, 1.0, 0.0)
        if config_factory else without_stage_block(config),
        seed,
    )
    cases = {
        "perturbed_non_p_input": run_engine(
            config_factory(nc.MODEL_M1, 1.4, 1.0, 0.0)
            if config_factory else
            with_stage_block(perturbed_config(config, 1.4), rho=1.0, p=0.0), seed
        ),
        "routine_hazard_scale_not_pinned": run_engine(
            config_factory(nc.MODEL_M1, 1.0, 0.8, 0.0)
            if config_factory else with_stage_block(config, rho=0.8, p=0.0), seed
        ),
    }
    evidence = {}
    unexpectedly_identical = []
    for name, result in cases.items():
        same, detail = identical(m0, result)
        evidence[name] = {"identical": bool(same), "detail": detail}
        if same:
            unexpectedly_identical.append(name)
    return CheckResult(
        "deliberate_failures_do_fail",
        not unexpectedly_identical,
        "both deliberate failures differ from M0, as they must"
        if not unexpectedly_identical else
        f"these did NOT differ from M0 and so cannot detect a regression: "
        f"{unexpectedly_identical}",
        evidence,
    )


def check_positive_p_is_live(
    config: Mapping, seed: int, *, values: Sequence[float] = (1e-18, 0.35),
    config_factory: GateConfigFactory | None = None,
) -> CheckResult:
    """A positive ``p`` must change the trajectory, however small.

    This is the evidence behind the prohibition of an exclusion band above
    zero: ``p = 1e-18`` leaves the identity branch and re-keys the RNG, so it
    is a different realisation -- which constrains how it may be *compared*,
    not whether the value is admissible.  It also proves the identity is a
    property of the dispatch rather than of an inert parameter.
    """
    m0 = run_engine(
        config_factory(nc.MODEL_M0, 1.0, 1.0, 0.0)
        if config_factory else without_stage_block(config),
        seed,
    )
    evidence = {}
    inert = []
    for value in values:
        same, detail = identical(
            m0, run_engine(
                config_factory(nc.MODEL_M1, 1.0, 1.0, float(value))
                if config_factory else with_stage_block(config, rho=1.0, p=float(value)),
                seed,
            )
        )
        evidence[repr(float(value))] = {"identical": bool(same), "detail": detail}
        if same:
            inert.append(float(value))
    return CheckResult(
        "positive_p_changes_the_trajectory",
        not inert,
        "every declared positive p changes the realised trajectory, so the identity "
        "rests on the exact-zero dispatch rather than on an inert parameter"
        if not inert else
        f"these positive p values left the trajectory unchanged, so M1 is inert and the "
        f"identity is vacuous: {inert}",
        evidence,
    )


# ==========================================================================
# The gate artifact
# ==========================================================================

def code_fingerprint(root: Path = ROOT) -> dict[str, str]:
    """Hash of every module that decides whether the identity holds."""
    return {name: ccs.sha256_file(root / name) for name in GATE_CODE_MODULES}


def run_gate(
    *,
    base_config: str | Path = DEFAULT_BASE_CONFIG,
    end_year: int = DEFAULT_END_YEAR,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    vector_multipliers: Sequence[float] = (1.0, 0.9, 1.15),
    root: Path = ROOT,
    config_factory: GateConfigFactory | None = None,
    route_evidence: Mapping[str, Any] | None = None,
) -> dict:
    """Run every check and return the gate artifact.

    All four checks run; the gate passes only if all four pass.  Recording
    only the identity would leave the gate satisfied by exactly the vacuous
    fixture it exists to prevent.
    """
    config = gate_config(base_config=base_config, end_year=end_year, root=root)
    if config_factory is not None:
        config = config_factory(nc.MODEL_M0, 1.0, 1.0, 0.0)
    started = time.time()
    checks = [
        check_not_vacuous(config, int(seeds[0])),
        check_identity(
            config, [int(s) for s in seeds], vectors=vector_multipliers,
            config_factory=config_factory,
        ),
        check_deliberate_failures(
            config, int(seeds[0]), config_factory=config_factory,
        ),
        check_positive_p_is_live(
            config, int(seeds[0]), config_factory=config_factory,
        ),
    ]
    passed = all(check.passed for check in checks)
    base_path = Path(base_config)
    resolved = base_path if base_path.is_absolute() else root / base_path
    return {
        "schema": GATE_VERSION,
        "passed": bool(passed),
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "elapsed_seconds": float(time.time() - started),
        "base_config": str(base_config),
        "base_config_sha256": ccs.sha256_file(resolved),
        "end_year": int(end_year),
        "seeds": [int(s) for s in seeds],
        "vector_multipliers": [float(m) for m in vector_multipliers],
        "code_sha256": code_fingerprint(root),
        "config_route": (
            "ProductionEvaluator.config_for" if config_factory else "legacy_raw_config"
        ),
        "route_evidence": dict(route_evidence or {}),
        "checks": [check.as_dict() for check in checks],
        "n_checks": len(checks),
        "n_passed": sum(1 for check in checks if check.passed),
        "environment": scientific_runtime_fingerprint(),
        "meaning": (
            "M0 and M1-at-exact-p=0 give bit-identical scientific outputs across the "
            "recorded vectors and seeds, on the recorded code. This is a prerequisite "
            "for launching M1; it is not an authorisation to run one."
        ),
        "limitation": (
            "the identity is demonstrated on a short truncated run, not on the full "
            "production horizon, and for the recorded vectors and seeds only"
        ),
    }


def _validate_gate_result_summary(artifact: Mapping[str, Any]) -> None:
    """Reject internally inconsistent or malformed gate evidence.

    ``passed`` is a derived summary, not an independent authorisation bit.  A
    certificate is valid only when its named results and redundant counts all
    describe the same outcome.  The redundancy is intentional: validating it
    makes accidental truncation, hand editing, and partial result assembly
    fail closed instead of silently authorising M1.
    """
    checks = artifact.get("checks")
    if not isinstance(checks, list):
        raise ContractError(
            "identity gate artifact has malformed checks; expected a list of "
            "named boolean results; re-run the gate"
        )

    names: list[str] = []
    results: list[bool] = []
    for index, check in enumerate(checks):
        if not isinstance(check, Mapping):
            raise ContractError(
                f"identity gate artifact check {index} is not an object; re-run the gate"
            )
        name = check.get("check")
        result = check.get("passed")
        if not isinstance(name, str) or not name:
            raise ContractError(
                f"identity gate artifact check {index} has no valid name; re-run the gate"
            )
        # Do not accept truthy strings or integers in scientific evidence.
        if type(result) is not bool:
            raise ContractError(
                f"identity gate artifact check {name!r} has non-boolean passed="
                f"{result!r}; re-run the gate"
            )
        names.append(name)
        results.append(result)

    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ContractError(
            f"identity gate artifact repeats check(s) {duplicates}; each check must "
            "appear exactly once; re-run the gate"
        )
    missing = sorted(set(REQUIRED_GATE_CHECKS) - set(names))
    if missing:
        raise ContractError(
            f"the identity gate artifact is missing check(s) {missing}, so it "
            "certifies less than this code requires; re-run the gate"
        )

    top_level_passed = artifact.get("passed")
    if type(top_level_passed) is not bool:
        raise ContractError(
            "identity gate artifact has a non-boolean top-level passed value; "
            "re-run the gate"
        )
    n_checks = artifact.get("n_checks")
    n_passed = artifact.get("n_passed")
    if type(n_checks) is not int or type(n_passed) is not int:
        raise ContractError(
            "identity gate artifact has non-integer n_checks/n_passed counts; "
            "re-run the gate"
        )

    observed_n_checks = len(checks)
    observed_n_passed = sum(results)
    observed_passed = all(results)
    contradictions = []
    if n_checks != observed_n_checks:
        contradictions.append(
            f"n_checks={n_checks}, but {observed_n_checks} checks are recorded"
        )
    if n_passed != observed_n_passed:
        contradictions.append(
            f"n_passed={n_passed}, but {observed_n_passed} checks passed"
        )
    if top_level_passed != observed_passed:
        contradictions.append(
            f"passed={top_level_passed}, but all recorded checks passed="
            f"{observed_passed}"
        )
    if contradictions:
        raise ContractError(
            "identity gate artifact contains contradictory result evidence: "
            + "; ".join(contradictions)
            + "; re-run the gate"
        )


def assert_gate_satisfied(
    path: str | Path, *, root: Path = ROOT,
    expected_base_config: str | Path | None = None,
    expected_plan_sha256: str | None = None,
    expected_scientific_config_sha256: str | None = None,
) -> dict:
    """Refuse an M1 design unless the gate passed on the code now on disk.

    Four separate refusals, because each failure mode is different:

    * no artifact -- the gate was never run;
    * ``passed`` false -- it ran and the identity does not hold;
    * a check missing -- the artifact predates a check, so it certifies less
      than the gate now requires;
    * a code hash mismatch -- the identity was proved against different code,
      which is exactly the case a stale artifact would hide.
    """
    gate_path = Path(path)
    if not gate_path.is_absolute():
        gate_path = root / gate_path
    if not gate_path.exists():
        raise ContractError(
            f"the M0/M1 identity gate artifact does not exist: {gate_path}. Run\n"
            "  .venv/bin/python run_robust_nroy_freeze.py identity-gate --out <dir>\n"
            "before preparing an M1 design. The identity is a production prerequisite "
            "(ROBUST_NROY_FREEZE_SPEC section 2) and the pytests that check it are "
            "SKIPPED under the no-engine guard, so a green suite does not establish it."
        )
    artifact = nc.strict_json_load(gate_path, what="identity-gate certificate")
    if not isinstance(artifact, Mapping):
        raise ContractError("identity-gate certificate must be a JSON object")
    if artifact.get("schema") != GATE_VERSION:
        raise ContractError(
            f"identity gate artifact has schema {artifact.get('schema')!r}, this code "
            f"requires {GATE_VERSION!r}; re-run the gate"
        )
    _validate_gate_result_summary(artifact)
    if not artifact["passed"]:
        failed = [
            c["check"] for c in artifact["checks"] if not c["passed"]
        ]
        raise ContractError(
            f"the M0/M1 identity gate did NOT pass: {failed or 'unknown check'}. "
            "M1 does not nest M0 under the recorded code, so no M1 design may be "
            "prepared. See the artifact for each check's detail."
        )
    if expected_plan_sha256 is not None:
        if artifact.get("config_route") != "ProductionEvaluator.config_for":
            raise ContractError(
                "the identity gate did not exercise ProductionEvaluator.config_for; "
                "a raw-config comparison cannot authorise production M1 work"
            )
        route = artifact.get("route_evidence") or {}
        if str(route.get("plan_sha256", "")) != str(expected_plan_sha256):
            raise ContractError(
                "the identity gate is not bound to this signed run plan; re-run the gate"
            )
        if expected_scientific_config_sha256 is not None and str(
            route.get("scientific_config_sha256", "")
        ) != str(expected_scientific_config_sha256):
            raise ContractError(
                "the identity gate is not bound to this scientific configuration; "
                "re-run the gate"
            )
        vectors = route.get("vectors")
        if not isinstance(vectors, Mapping) or not vectors:
            raise ContractError(
                "the identity gate records no production-routed work-unit vectors"
            )
    current = code_fingerprint(root)
    stale = {
        name: (artifact.get("code_sha256", {}).get(name), current[name])
        for name in GATE_CODE_MODULES
        if artifact.get("code_sha256", {}).get(name) != current[name]
    }
    if stale:
        raise ContractError(
            "the identity gate was proved against different code and is stale:\n"
            + "\n".join(
                f"  - {name}: gate {was}, now {now}" for name, (was, now) in stale.items()
            )
            + "\nRe-run the gate. A stale artifact is exactly what would hide a "
            "regression in the identity dispatch."
        )
    recorded_runtime = artifact.get("environment")
    current_runtime = scientific_runtime_fingerprint()
    if recorded_runtime != current_runtime:
        raise ContractError(
            "the identity gate was proved under a different scientific runtime and "
            "is stale:\n"
            f"  - gate: {recorded_runtime!r}\n"
            f"  - now:  {current_runtime!r}\n"
            "Re-run the gate under the runtime that will execute production."
        )
    base = Path(str(artifact.get("base_config") or ""))
    if not base.is_absolute():
        base = root / base
    recorded_base = str(artifact.get("base_config_sha256") or "")
    if not base.exists() or not recorded_base or ccs.sha256_file(base) != recorded_base:
        raise ContractError(
            "the identity gate base configuration is missing or changed since the gate "
            "ran; re-run the gate against the signed production configuration"
        )
    if expected_base_config is not None:
        expected = Path(expected_base_config)
        if not expected.is_absolute():
            expected = root / expected
        if not expected.exists() or ccs.sha256_file(expected) != recorded_base:
            raise ContractError(
                "the identity gate was not run against the signed production base "
                "configuration; re-run the gate with --base-config set to the plan's "
                "models.base_config"
            )
    return artifact


__all__ = [name for name in dir() if not name.startswith("_")]
