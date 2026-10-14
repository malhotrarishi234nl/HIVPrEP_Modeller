"""
Model-output regression tests, established 2026-09-14.

THE GAP THESE CLOSE
-------------------
Before this file, **not one test in the repository asserted a model output**. The
audit on 2026-09-14 found that every green pin measured something the production
changes structurally could not move -- RNG key derivation (the four A-13 pins),
sampler moments on synthetic inputs (A-28), config reachability (A-12 guards),
static AST structure (A-20), the v6 config golden dict -- while every harness that
DID compare model output to a stored reference was pinned to a pre-A-28 world and is
now failing (A-16, A-9, A-21, A-22, B-8) or, worse, silently stale (`run_a13`,
`run_a14`, `run_a15` read pre-change references without asserting).

Net effect: a regression in the model itself would have been caught by nothing.

WHAT A FAILURE HERE MEANS
-------------------------
Model output moved. That is all it means. It does NOT mean the new output is wrong,
and it does NOT mean the baseline was right -- see the warning below. The correct
response is to find out WHAT changed and say why, using the per-scalar assertions to
localise it, then either fix the regression or re-baseline **deliberately** with a
recorded reason.

WHAT IT MUST NEVER MEAN
-----------------------
"Regenerate the baseline until it passes." That inverts the purpose of the file and
is prohibited by §1A.4. `write_baseline()` demands a reason string for exactly this
reason.

THE BASELINE IS NOT A CORRECTNESS CERTIFICATE
---------------------------------------------
It records what the model produced on 2026-09-14, not what it should produce.
Several pinned values are known provisional: C-1 reproduces the observed non-MSM
series at 101.1% rather than 100% (injected people transmit before ART);
`infections_averted` variance rose 1.58-1.96x; A-3's closure criterion 1 now fails at
6.57 SE. Those properties are pinned so they cannot drift unnoticed -- not blessed.
**Do not quote a baseline value as a result.**
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.postchange_baseline import (
    BASELINE_PATH,
    METRICS,
    TRACKED_MODULES,
    annual_digest,
    load_baseline,
)

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[1]


# ── the baseline artefact itself ─────────────────────────────────────────────


def test_baseline_artefact_exists_and_records_its_provenance():
    """
    A baseline with no recorded reason is indistinguishable from a silent
    re-baseline, which is the failure mode this collection exists to prevent.
    """
    if not BASELINE_PATH.exists():
        pytest.skip("baseline not yet generated; run tools/postchange_baseline.py write")
    payload = json.loads(BASELINE_PATH.read_text())
    assert payload["reason"].strip(), "baseline must record WHY it was written"
    assert payload["generated_utc"]
    assert "REGRESSION DETECTOR" in payload["warning"], (
        "the baseline must carry its own warning that it is not a correctness "
        "certificate, because it will outlive the context in which it was made"
    )
    assert "ADDENDUM" in payload["supersedes"], (
        "this baseline must not claim to supersede the pre-change references"
    )


def test_baseline_does_not_disturb_the_pre_change_references():
    """
    The user's standing decision: addendum only. The stale digest assertions are
    left failing DELIBERATELY as standing flags. If someone 'helpfully' updates
    them to the current digest, the flag is lost -- so pin that they still hold
    the original A-20-era value.
    """
    stale = "8d186b359195f3a911e5d53577b0cc900273466694356e513a9284c40197a455"
    for name in ("tools/run_a21_rare_stratum_starvation.py",
                 "tools/run_a22_background_mortality_grouping.py"):
        path = ROOT / name
        if not path.exists():
            continue
        assert stale in path.read_text(), (
            f"{name} no longer asserts the pre-change digest. If that was a "
            "deliberate re-baseline it needs recording; if it was done to make a "
            "test pass, it destroyed a standing flag."
        )


# ── the model output ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def baseline():
    if not BASELINE_PATH.exists():
        pytest.skip("baseline not yet generated; run tools/postchange_baseline.py write")
    return load_baseline()["baseline"]


@pytest.fixture(scope="module")
def current():
    from tools.postchange_baseline import capture

    return capture(include_stochastic=True)


def test_deterministic_digest_is_unchanged(baseline, current):
    """Whole-model fingerprint over the seven pinned annual series."""
    assert current["deterministic"]["digest"] == baseline["deterministic"]["digest"], (
        "the deterministic model output changed. Use the per-scalar tests below to "
        "localise it. Do NOT regenerate the baseline to make this pass."
    )


@pytest.mark.parametrize("scalar", [
    "new_diagnoses_1975_1995",
    "new_diagnoses_1996_2024",
    "new_diagnoses_msm_1996_2024",
    "new_diagnoses_non_msm_1996_2024",
    "new_infections_1975_2025",
    "aids_deaths_1975_2025",
    "PLHIV_2024",
    "on_art_stock_2024",
    "plhiv_female_15plus_2024",
    "new_infections_msm_total",
    "new_infections_non_msm_total",
])
def test_deterministic_scalar_is_unchanged(baseline, current, scalar):
    """
    Exact equality, deliberately. Deterministic mode consumes no randomness, so
    there is no tolerance to widen -- any drift is a real change in behaviour and
    a tolerance here would only hide it.
    """
    was = baseline["deterministic"]["scalars"][scalar]
    now = current["deterministic"]["scalars"][scalar]
    assert now == was, f"{scalar}: baseline {was} -> current {now}"


def test_stochastic_per_seed_digests_are_unchanged(baseline, current):
    """
    Catches RNG-path regressions that deterministic mode cannot see -- the
    deterministic allocator and the A-28 coupled samplers are different code.
    """
    if "stochastic" not in baseline:
        pytest.skip("baseline captured without the stochastic tier")
    for seed, was in baseline["stochastic"]["per_seed"].items():
        now = current["stochastic"]["per_seed"][seed]
        assert now["digest"] == was["digest"], (
            f"stochastic seed {seed} digest changed: {was['digest']} -> {now['digest']}"
        )


# ── attribution ──────────────────────────────────────────────────────────────


def test_tracked_module_fingerprints_are_recorded(baseline):
    """
    Every module that drives output is hashed, so a digest mismatch can be
    ATTRIBUTED rather than merely observed. A missing entry means a new
    result-driving module was added without being tracked.
    """
    recorded = set(baseline["code_fingerprints"])
    assert recorded == set(TRACKED_MODULES), (
        f"tracked-module set drifted: missing {set(TRACKED_MODULES) - recorded}, "
        f"unexpected {recorded - set(TRACKED_MODULES)}"
    )
    for name in TRACKED_MODULES:
        assert (ROOT / name).exists(), f"tracked module {name} no longer exists"


def test_digest_covers_the_metrics_it_claims_to(baseline):
    assert tuple(baseline["metrics"]) == METRICS


def test_digest_is_sensitive_to_a_changed_value():
    """
    A fingerprint that cannot fail is worse than none. Prove it responds.
    """
    annual = {m: {1990: 1.0, 1991: 2.0} for m in METRICS}
    before = annual_digest(annual)
    annual["new_diagnoses"][1990] = 1.0000001
    assert annual_digest(annual) != before
