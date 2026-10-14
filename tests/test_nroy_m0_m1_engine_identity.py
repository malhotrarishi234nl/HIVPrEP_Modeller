"""M0 versus M1-at-``p=0`` identity, through the real engine.

``ROBUST_NROY_FREEZE_SPEC_2026-10-14.md`` section 2 requires that "an automated
multi-vector, multi-seed identity test must show that this boundary executes the
legacy path and gives identical scientific outputs before M1 is launched", and
section 9 requires "exact M0 versus M1-at-``p=0`` identity across multiple
vectors and seeds, plus deliberate failure when any non-``p`` input differs".

This module closes a real gap in the existing coverage.
``tests/test_stage_structured_diagnosis_audit.py`` compares ``enabled=false``
against ``enabled=true, p=0``, which is the right check for the identity
*dispatch*; and the harness smoke path in
``run_stage_structured_diagnosis_focused_calibration.smoke()`` compares hashes
against a true no-block control, but needs frozen prior outputs.  Neither is a
pytest that compares a genuine **no-block M0 configuration** against
``enabled=true, p=0`` inside the engine across several vectors and seeds.  That
is what the tests below do.

Why the identity is not merely arithmetic
-----------------------------------------
The engine dispatches on ``StageStructuredDiagnosis.is_identity``, which is
``routine_hazard_scale == 1.0 and aids_symptom_annual_probability == 0.0``
(``stage_structured_diagnosis.py``).  When that holds, ``_step_diagnosis``
(``run_v3.py``) never enters the stage-structured branch and the legacy
diagnosis code runs with the legacy counter-based RNG streams
(``diagnosis_non_msm`` / ``diagnosis_msm``).

This matters because the RNG is counter-based and **keyed by process name**
(``counter_based_rng.py``), so running the new branch with a mathematically
identical zero probability would draw from differently keyed streams and
produce a different realised trajectory.  The identity therefore holds at
*exactly* ``p = 0`` and because of the dispatch, not because zero is a
no-op.  These tests assert the consequence rather than the mechanism, so they
would catch a future refactor that removed the short-circuit.

EXECUTION BOUNDARY
------------------
Every test here constructs a real ``SimulationEngineV3``, so all of them are
**skipped** under the repository's no-engine guard:

    .venv/bin/python -m pytest -p pytest_no_engine_guard -rs tests/

They must be run by the user, without that guard, before M1 is launched:

    .venv/bin/python -m pytest tests/test_nroy_m0_m1_engine_identity.py -q
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nroy_fixtures as fx  # noqa: E402
from nroy_fixtures import ROOT  # noqa: E402

import nroy_contract as nc  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402
from stage_structured_diagnosis import (  # noqa: E402
    load_stage_structured_diagnosis,
)

PROFILE_PATH = ("transmission_model", "disease", "diagnosis", "stage_structured_diagnosis")

#: Several seeds: the identity must hold per realised trajectory, not only in
#: distribution.
SEEDS = (9182, 20260101, 777003)


#: End year for the identity runs.
#:
#: This value is load-bearing and must not be lowered. ``1980_test.json``
#: natively runs to 2025; truncating it to 1982 produces an epidemic that never
#: takes off (peak prevalence 0.00%, zero diagnoses), under which M0 and M1
#: agree for *any* ``p`` and *any* routine hazard scale. The identity would
#: then be trivially true and this whole module would prove nothing.
#:
#: At 1990 the run is ~1s and produces a few hundred diagnoses, which is enough
#: for the AIDS-symptom mechanism to bite: p=0.35 and rho=0.8 both change the
#: output, while p=0 stays bit-identical.
#: ``test_the_identity_fixture_is_not_vacuous`` enforces this.
IDENTITY_END_YEAR = 1990


def _short_config() -> dict:
    """A short stochastic run.  Stochastic mode is the case that can break."""
    config = json.loads((ROOT / "configs/1980_test.json").read_text())
    config["run_control"]["end_year"] = {"value": IDENTITY_END_YEAR}
    config["run_control"]["deterministic_mode"] = {"value": False}
    return config


def _without_stage_block(config: dict) -> dict:
    """A genuine M0 configuration: the block is absent, not merely disabled."""
    out = copy.deepcopy(config)
    node = out
    for key in PROFILE_PATH[:-1]:
        node = node[key]
    node.pop(PROFILE_PATH[-1], None)
    return out


def _with_stage_block(config: dict, *, rho: float, p: float, start_year: int = 1985) -> dict:
    out = copy.deepcopy(config)
    node = out
    for key in PROFILE_PATH[:-1]:
        node = node[key]
    node[PROFILE_PATH[-1]] = {
        "enabled": {"value": True},
        "routine_hazard_scale": {"value": float(rho)},
        "aids_symptom_annual_probability": {"value": float(p)},
        "symptom_start_year": {"value": int(start_year)},
    }
    return out


def _perturbed_config(config: dict, factor: float) -> dict:
    """Change one non-``p`` input: the MSM orientation contact multiplier."""
    out = copy.deepcopy(config)
    node = out["transmission_model"]["groups"]["advanced"][
        "orientation_contact_multipliers"]["value"]
    node["MSM"] = float(node["MSM"]) * factor
    return out


def _run(config: dict, seed: int) -> dict:
    engine = SimulationEngineV3(config, seed=seed)
    engine.output_writer.enabled = False
    return engine.run()


def _assert_identical(left: dict, right: dict, *, what: str) -> None:
    np.testing.assert_array_equal(
        left["final_state"], right["final_state"],
        err_msg=f"{what}: final_state differs",
    )
    pd.testing.assert_frame_equal(
        left["summary"], right["summary"], check_exact=True,
        obj=f"{what}: summary",
    )


# ==========================================================================
# The identity dispatch itself (no engine; always runs)
# ==========================================================================

def _engine_shaped(block: dict | None) -> dict:
    """An engine-shaped config fragment.

    ``load_stage_structured_diagnosis`` reads the *engine* path
    ``disease.diagnosis.stage_structured_diagnosis``.  The raw JSON path
    ``transmission_model.disease.diagnosis...`` is translated by
    ``config_reader`` during engine construction, so a unit test of the loader
    must use the engine shape rather than the raw one.
    """
    diagnosis: dict = {}
    if block is not None:
        diagnosis["stage_structured_diagnosis"] = block
    return {"disease": {"diagnosis": diagnosis}}


def _block(rho: float, p: float, start_year: int | None = 1985) -> dict:
    out = {
        "enabled": {"value": True},
        "routine_hazard_scale": {"value": float(rho)},
        "aids_symptom_annual_probability": {"value": float(p)},
    }
    if start_year is not None:
        out["symptom_start_year"] = {"value": int(start_year)}
    return out


def test_is_identity_requires_both_p_zero_and_unit_routine_scale():
    """Both halves of ``is_identity`` matter; this needs no engine."""
    assert load_stage_structured_diagnosis(
        _engine_shaped(_block(rho=1.0, p=0.0))
    ).is_identity
    assert not load_stage_structured_diagnosis(
        _engine_shaped(_block(rho=0.8, p=0.0))
    ).is_identity, "p=0 with a rescaled routine hazard is NOT the identity"
    assert not load_stage_structured_diagnosis(
        _engine_shaped(_block(rho=1.0, p=1e-18))
    ).is_identity, "the dispatch compares p to exactly 0.0"


def test_absent_block_loads_as_disabled_identity():
    profile = load_stage_structured_diagnosis(_engine_shaped(None))
    assert not profile.enabled
    assert profile.aids_symptom_annual_probability == 0.0
    assert profile.is_identity


def test_the_raw_config_helpers_write_the_block_the_reader_translates():
    """Guard that the raw-path helpers used by the engine tests are correct."""
    raw = _with_stage_block(_short_config(), rho=1.0, p=0.0)
    node = raw
    for key in PROFILE_PATH:
        assert key in node, f"missing raw config path element {key!r}"
        node = node[key]
    assert node["aids_symptom_annual_probability"]["value"] == 0.0
    assert PROFILE_PATH[-1] not in _without_stage_block(raw)[
        "transmission_model"]["disease"]["diagnosis"]


# ==========================================================================
# Engine identity (skipped under the no-engine guard; user-run)
# ==========================================================================

def test_the_identity_fixture_is_not_vacuous():
    """The identity is only meaningful if the mechanism can actually fire.

    This is the guard that matters most in this module. An identity test run on
    a configuration where the epidemic never takes off passes for the wrong
    reason: M0 and M1 then agree for *any* ``p``, every other test here is
    green, and the module proves nothing.

    Rather than matching column names loosely, this asserts the two concrete
    preconditions of the AIDS-symptom mechanism: that infections occur at all,
    and that there is a non-empty **undiagnosed AIDS-stage** pool for the
    symptom hazard to act on.
    """
    summary = _run(_without_stage_block(_short_config()), SEEDS[0])["summary"]

    required = ("new_infections_msm", "aids_undiagnosed_start", "msm_undiagnosed_hiv_pos")
    missing = [c for c in required if c not in summary.columns]
    assert not missing, (
        f"the summary no longer carries {missing}; this guard needs updating "
        f"against the current schema: {list(summary.columns)[:40]}"
    )

    infections = float(summary["new_infections_msm"].sum())
    assert infections > 0, (
        f"the identity fixture produced {infections} MSM infections, so the "
        "epidemic never took off and M0 == M1 holds trivially for any p; "
        "raise IDENTITY_END_YEAR"
    )

    aids_pool = float(summary["aids_undiagnosed_start"].sum())
    assert aids_pool > 0, (
        f"the undiagnosed AIDS-stage pool is {aids_pool}, so the AIDS-symptom "
        "hazard has nothing to act on and the identity is vacuous; "
        "raise IDENTITY_END_YEAR"
    )


def test_a_positive_p_is_detectable_at_this_horizon():
    """Pair to the vacuity gate: the mechanism must be observable here.

    If this fails, the horizon is too short for the AIDS-symptom hazard to
    change anything and the identity tests are vacuous even though they pass.
    """
    base = _short_config()
    m0 = _run(_without_stage_block(base), SEEDS[0])
    active = _run(_with_stage_block(base, rho=1.0, p=0.35), SEEDS[0])
    with pytest.raises(AssertionError):
        _assert_identical(m0, active, what="p = 0.35 must be detectable")


@pytest.mark.parametrize("seed", SEEDS)
def test_no_block_m0_equals_enabled_p_zero_m1_across_seeds(seed):
    """The required identity control, per seed, on a genuine no-block M0."""
    base = _short_config()
    m0 = _run(_without_stage_block(base), seed)
    m1 = _run(_with_stage_block(base, rho=1.0, p=0.0), seed)
    _assert_identical(m0, m1, what=f"seed {seed}")


@pytest.mark.parametrize("factor", [0.85, 1.0, 1.25])
def test_identity_holds_across_multiple_parameter_vectors(factor):
    """Multiple vectors: the identity must not depend on one lucky baseline."""
    base = _perturbed_config(_short_config(), factor)
    seed = SEEDS[0]
    m0 = _run(_without_stage_block(base), seed)
    m1 = _run(_with_stage_block(base, rho=1.0, p=0.0), seed)
    _assert_identical(m0, m1, what=f"vector factor {factor}")


def test_identity_holds_for_every_vector_and_seed_combination():
    """The full multi-vector, multi-seed grid the specification asks for."""
    for factor in (0.9, 1.1):
        base = _perturbed_config(_short_config(), factor)
        for seed in SEEDS:
            m0 = _run(_without_stage_block(base), seed)
            m1 = _run(_with_stage_block(base, rho=1.0, p=0.0), seed)
            _assert_identical(m0, m1, what=f"factor {factor}, seed {seed}")


def test_identity_is_independent_of_the_symptom_start_year_at_p_zero():
    base = _short_config()
    seed = SEEDS[0]
    m0 = _run(_without_stage_block(base), seed)
    for start_year in (1985, 2000):
        m1 = _run(_with_stage_block(base, rho=1.0, p=0.0, start_year=start_year), seed)
        _assert_identical(m0, m1, what=f"start_year {start_year}")


# ---- deliberate failures -------------------------------------------------

def test_identity_fails_deliberately_when_a_non_p_input_differs():
    """Required by section 9: the test must fail when it should."""
    base = _short_config()
    seed = SEEDS[0]
    m0 = _run(_without_stage_block(base), seed)
    m1 = _run(_with_stage_block(_perturbed_config(base, 1.4), rho=1.0, p=0.0), seed)
    with pytest.raises(AssertionError):
        _assert_identical(m0, m1, what="perturbed non-p input")


def test_identity_fails_when_the_routine_hazard_scale_is_not_pinned():
    """``p = 0`` with a rescaled routine hazard takes the new branch."""
    base = _short_config()
    seed = SEEDS[0]
    m0 = _run(_without_stage_block(base), seed)
    m1 = _run(_with_stage_block(base, rho=0.8, p=0.0), seed)
    with pytest.raises(AssertionError):
        _assert_identical(m0, m1, what="rho != 1 at p=0")


def test_a_p_just_above_zero_changes_the_trajectory_discontinuously():
    """Why the contract rejects probes in ``(0, epsilon)`` instead of snapping.

    ``p = 1e-18`` is physically negligible but leaves the identity branch, so
    the engine draws from differently keyed RNG streams. The realised
    trajectory changes even though the step probability barely moves. A probe
    there would be scientifically meaningless, which is why
    ``nroy_contract.p_discontinuity_reason`` refuses it rather than rounding it
    to zero.
    """
    base = _short_config()
    seed = SEEDS[0]
    m0 = _run(_without_stage_block(base), seed)
    nudged = _run(_with_stage_block(base, rho=1.0, p=1e-18), seed)
    with pytest.raises(AssertionError):
        _assert_identical(m0, nudged, what="p = 1e-18")

    # ...and the contract refuses such a vector up front.
    m1_space = fx.m1_space()
    vector = nc.embed_m0_in_m1(fx.m0_space(), m1_space, fx.interior_vector(fx.m0_space()))
    vector[fx.P_NAME] = 1e-18
    assert nc.p_discontinuity_reason(vector, m1_space, identity_epsilon=1e-6) is not None


def test_a_positive_p_actually_changes_the_model():
    """Guards against a vacuous identity: M1 must not be inert for p > 0."""
    base = _short_config()
    seed = SEEDS[0]
    m0 = _run(_without_stage_block(base), seed)
    active = _run(_with_stage_block(base, rho=1.0, p=0.35), seed)
    with pytest.raises(AssertionError):
        _assert_identical(m0, active, what="p = 0.35")
