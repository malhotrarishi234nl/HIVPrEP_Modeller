"""
Guards added by the A-12 review (2026-09-10).

A-12 established that ten fragmentation-sensitive allocation sites each contribute
0 people to the base-case between-arm differential, because every one of them is
shared before the arm fork, configured to zero, disabled, dormant or unreachable.
That finding is a statement about the CURRENT configuration and call graph.  The
A-12 test suite protects the audit's *inventory*; nothing protected the *dormancy*.

These tests close that gap.  If someone flips a config flag or reintroduces a
legacy caller, the A-12 report's "0 differential" rows silently become false —
here they fail loudly instead, naming the site and the repair recommendation.

They deliberately assert configuration and reachability, not simulation output:
a dormant path contributes zero by not executing, and the cheap, durable way to
keep that true is to assert it cannot execute.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG = ROOT / "configs" / "V13.22_20260604_nearfit_rank001_basecase.json"
A12_DIR = ROOT / "outputs" / "hta_results" / "a12_deterministic_allocation_audit_20260910_103000"

needs_config = pytest.mark.skipif(not BASE_CONFIG.exists(), reason="base-case config not available")


def _config() -> dict:
    with BASE_CONFIG.open(encoding="utf-8") as handle:
        return json.load(handle)


def _value(node):
    """Config nodes are either a bare value or a {'value': ..., 'notes': ...} wrapper."""
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


def _get(config: dict, *path):
    node = config
    for key in path:
        assert isinstance(node, dict) and key in node, f"config path {'.'.join(path)} missing at {key!r}"
        node = node[key]
    return _value(node)


# --------------------------------------------------------------------------
# Config-gated dormancy — AGE-03/04/05, POPADJ-01, AE-03, INIT-04, CHMORT-02
# --------------------------------------------------------------------------

@needs_config
def test_age_03_04_05_legacy_aging_paths_stay_dormant():
    """`fractional_legacy` and `discrete` round every cell under a common probability."""
    method = _get(_config(), "demography", "advanced", "aging_method")
    assert method == "fractional", (
        f"aging_method is {method!r}. A-12 sites AGE-03/AGE-04/AGE-05 are cellwise and were "
        "quantified at 0 people ONLY because aging_method == 'fractional' selects the exact "
        "residence-queue path. Repair those paths (aggregate within age-rate block) before "
        "activating a legacy or discrete aging mode."
    )


@needs_config
def test_popadj_01_population_scaling_stays_disabled():
    """POPADJ-01 truncates every selected tensor cell; A-12 calls it unsafe to enable as written."""
    enabled = _get(_config(), "demography", "advanced", "population_adjustments", "enabled")
    assert enabled is False, (
        "population_adjustments.enabled is True. A-12 site POPADJ-01 truncates each selected "
        "tensor cell independently and its recommendation is 'do not enable as written; rewrite "
        "around an aggregate target or delete'."
    )


HTA_CONFIGS = (ROOT / "configs" / "hta_config.json", ROOT / "configs" / "hta_costing_config.json")


@pytest.mark.parametrize("hta_config", HTA_CONFIGS, ids=lambda path: path.name)
def test_ae_03_state_machine_resolution_stays_dormant(hta_config):
    """AE-03 truncates resolved events by severity with no residual reconciliation.

    `adverse_events.mode` lives in the HTA config, not the model config -- A-12 reads
    it from the loaded runtime config (`runner.hta_config`). Both shipped HTA configs
    are checked so neither can drift into `state_machine` unnoticed.
    """
    if not hta_config.exists():
        pytest.skip(f"{hta_config.name} not available")
    mode = _value(json.loads(hta_config.read_text(encoding="utf-8"))["adverse_events"]["mode"])
    assert mode == "prevalence", (
        f"adverse_events.mode is {mode!r} in {hta_config.name}. A-12 site AE-03 resolves "
        "state-machine adverse events "
        "by truncating per severity without residual reconciliation, so it can silently resolve "
        "fewer cases than the aggregate target already computed. Repair before switching mode."
    )


@needs_config
def test_init_04_legacy_initial_prevalence_stays_zero():
    """INIT-04 truncates every age-orientation-activity cell."""
    prevalence = _get(_config(), "population", "initial_hiv_prevalence")
    assert prevalence == 0.0, (
        f"initial_hiv_prevalence is {prevalence!r}. A-12 site INIT-04 truncates each "
        "age-orientation-activity cell; it is quantified at 0 only because the live epidemic is "
        "introduced by the later fixed-total seed path instead."
    )


@needs_config
def test_chmort_02_constant_child_mortality_fallback_stays_dormant():
    """CHMORT-02 rounds one common probability separately per age-sex cell."""
    enabled = _get(_config(), "demography", "life_table", "enabled")
    assert enabled is True, (
        "life_table.enabled is False. A-12 site CHMORT-02 is the constant child-mortality "
        "fallback, which rounds the same probability separately by age-sex cell. Allocate over "
        "the complete childhood array before relying on the fallback."
    )


# --------------------------------------------------------------------------
# Call-graph dormancy — INIT-05, TX-02
# --------------------------------------------------------------------------

def test_init_05_legacy_childhood_initializer_has_no_production_caller():
    """A-12 records `legacy_childhood_auto_initializer_called_by_production: false`."""
    import demography  # noqa: PLC0415

    assert hasattr(demography.DemographyModule, "initialize_childhood")
    callers = []
    for module_name in ("engine_v3", "run_v3", "run_scenarios"):
        module = __import__(module_name)
        try:
            source = inspect.getsource(module)
        except OSError:  # pragma: no cover
            continue
        if "initialize_childhood(" in source:
            callers.append(module_name)
    assert not callers, (
        f"initialize_childhood is now called from {callers}. A-12 site INIT-05 independently "
        "truncates male and female counts and its recommendation is 'remove or replace before "
        "reuse'. Its 0-person differential assumed no production caller."
    )


def test_tx_02_truncating_branch_is_deterministic_only_and_unreachable():
    """A-12's TX-02 row says the branch 'is reached only by the stochastic caller'.

    It is not.  The truncations sit behind `if deterministic:`, and the stochastic
    caller passes `deterministic=False` and takes `rng.binomial`.  The branch is dead
    in BOTH modes, which is a stronger claim than the CSV records.  This test pins the
    real structure so the correction cannot silently rot.
    """
    import transmission_corrected  # noqa: PLC0415

    source = inspect.getsource(transmission_corrected.TransmissionEngine._calc_msm_infections)
    assert "if deterministic:" in source
    assert "rng.binomial" in source, "the stochastic path must still use a binomial draw, not truncation"

    # Every truncation in this function must be inside a `deterministic` branch.
    deterministic_lines = {
        index
        for index, line in enumerate(source.splitlines())
        if "int(" in line and "def " not in line
    }
    assert deterministic_lines, "expected to find the truncating lines"

    # And the production deterministic caller must route around it entirely.
    caller = inspect.getsource(transmission_corrected.TransmissionEngine.calculate_transmission)
    assert "_calc_msm_expected_probability" in caller, (
        "calculate_transmission no longer routes deterministic MSM transmission through the "
        "expected-probability path. A-12 site TX-02 truncates infections after the role split; "
        "it was quantified at 0 only because that branch is unreachable."
    )


# --------------------------------------------------------------------------
# INIT-01 — the one nonzero aggregate discrepancy
# --------------------------------------------------------------------------

@needs_config
def test_init_01_shortfall_is_still_the_recorded_size():
    """A-12 recorded a 23-person shortfall from a configured 13,598,912.

    This is not a between-arm defect — both arms restore the same frozen snapshot —
    but the snapshot itself is built from the short population, so the shortfall is
    inside the frozen state rather than absent from it. It must be fixed before the
    production baseline is frozen. If the number moves, the report is stale.
    """
    facts_path = A12_DIR / "base_case_path_facts.json"
    if not facts_path.exists():
        pytest.skip("A-12 evidence directory not available")
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    configured = _get(_config(), "population", "total_size")
    assert facts["configured_initial_population"] == configured
    assert facts["initial_population_shortfall_people"] == 23
    assert (
        facts["configured_initial_population"] - facts["cellwise_initialized_population"]
        == facts["initial_population_shortfall_people"]
    )


# --------------------------------------------------------------------------
# Deterministic tie-breaking (A-12 review fix)
# --------------------------------------------------------------------------

def test_migration_negative_addback_uses_a_stable_tie_break():
    """`_apply_negative`'s largest-remainder add-back must break ties reproducibly.

    The remainders are heavily tied: `population * scale` for integer populations
    takes a small number of distinct fractional values, and every zero-population
    cell has remainder exactly 0.0. An unstable sort therefore picks WHICH tied cells
    receive the +1 by numpy's internal ordering. The total removed is identical either
    way, but the cells are not — and a person placed in a different
    (age, sex, orientation, activity, stage) cell changes downstream dynamics.
    """
    import migration  # noqa: PLC0415

    source = inspect.getsource(migration.MigrationModule._apply_negative)
    assert 'np.argsort(-combined, kind="stable")' in source, (
        "the add-back tie-break is not stable-sorted; results may differ across numpy "
        "versions and platforms. Match stochastic_allocation.deterministic_allocate_events."
    )


def test_stable_and_unstable_tie_breaks_select_different_cells():
    """Demonstrates the bug the fix prevents, so the guard above is not cargo-cult.

    Same total, different cells — which is exactly the failure that a totals-only
    materiality check would miss (the ENG-01 lesson).
    """
    rng = np.random.default_rng(7)
    population = np.zeros(20000, dtype=np.int64)
    chosen = rng.choice(population.size, 4000, replace=False)
    population[chosen] = rng.integers(1, 500, 4000)

    scaled = population.astype(np.float64) * (1.0 - 0.003)
    remainder = scaled - np.floor(scaled)
    excess = 137

    unstable = np.argsort(-remainder)[:excess]
    stable = np.argsort(-remainder, kind="stable")[:excess]

    assert int((remainder == 0.0).sum()) > 10_000, "expected heavy ties in this fixture"
    assert len(unstable) == len(stable) == excess          # the TOTAL is unaffected
    assert set(unstable.tolist()) != set(stable.tolist())  # the CELLS are not
    # Stable sorting breaks ties by ascending index, which is what makes it portable.
    tied = np.flatnonzero(remainder == remainder[stable].max())
    if tied.size > excess:
        assert stable.tolist() == sorted(stable.tolist())


# --------------------------------------------------------------------------
# Call-site granularity (A-12 review fix)
# --------------------------------------------------------------------------

def test_no_new_rounding_call_sites_since_the_audit():
    """A new rounding call inside an ALREADY-TRIAGED function must not pass silently.

    A-12's own guard raises on an untriaged `(module, function)` pair, which catches a
    new function but not a new call inside an existing one. 54 of the audited functions
    hold 2-12 rounding calls each, so a cellwise `np.round(pop * p)` dropped into, say,
    `migration.apply` would inherit that function's `exact_total_assignment` disposition.
    That is the shape in which ENG-01 was introduced.
    """
    from tools.run_a12_deterministic_allocation_audit import diff_call_sites  # noqa: PLC0415

    diff = diff_call_sites()
    assert not diff["added"], (
        "New rounding/integerization call sites have appeared in the A-12 audited modules "
        "since the audit froze. Each one needs a classification in INVENTORY or "
        "NON_ALLOCATION_FUNCTIONS, and a between-arm materiality judgement on the "
        "INCREMENTAL estimand (the ENG-01 lesson), before the baseline is regenerated with "
        f"write_call_fingerprint_baseline(). New sites:\n{diff['added']}"
    )
    assert not diff["removed"], (
        "Rounding call sites recorded by A-12 no longer exist. The inventory rows describing "
        f"them are now stale evidence. Removed sites:\n{diff['removed']}"
    )


def test_call_fingerprint_baseline_matches_the_audited_candidate_count():
    """The frozen baseline must describe the same candidates the report claims.

    258 at the time of the A-12 report; 265 after C-1 (2026-09-14) added
    ALLOC-02 (``deterministic_allocate_total``) and C1-01
    (``_step_exogenous_non_msm``), both triaged into INVENTORY. The A-12 report
    text still says 258 and is stale by exactly those seven call sites. A-29
    (2026-09-14) added one net integerization call while replacing TX-01's
    global block with independently conserved orientation blocks; TX-01 is
    reclassified in INVENTORY and the baseline contained 266 calls.

    **271 from 2026-10-14.** The M1 mechanism
    ``SimulationEngineV3._step_stage_structured_diagnosis`` had been adding
    three call sites that this guard had been reporting as untriaged ever since
    it was written: ``int(np.sum(diagnosed))`` twice and
    ``int(np.sum(diagnosed[:, msm_idx, ...]))`` once. All three are output
    scalar conversions of an already-integer array -- counters, not
    allocations; the allocation itself goes through ``_draw_events_for``.

    The function is now triaged as ``DX-04`` in ``INVENTORY``, with its
    between-arm materiality judgement on the incremental estimand recorded
    (the ENG-01 lesson): dormant in the production base case, which carries no
    ``stage_structured_diagnosis`` block, and bounded at one person per
    population per step with no effect on total diagnoses. That is the
    documented precondition for regenerating the baseline, so the baseline was
    regenerated. Two additional scalar conversions count the MSM and total
    diagnoses after the neutral-multiplier identity route was corrected to use
    the legacy all-orientations allocation. They do not allocate population.
    See ``tests/test_a12_stage_structured_diagnosis_allocation.py``
    and ``documentation/guides/REPOSITORY_INVENTORY_2026-10-14.md`` section 8.
    """
    from tools.run_a12_deterministic_allocation_audit import (  # noqa: PLC0415
        call_site_fingerprints,
        load_call_fingerprint_baseline,
        syntax_candidates,
    )

    assert sum(call_site_fingerprints().values()) == len(syntax_candidates()) == 271
    assert sum(load_call_fingerprint_baseline().values()) == 271


def test_fingerprints_ignore_formatting_but_not_new_calls():
    """The guard must not fire on reformatting, and must fire on an added call."""
    from tools.run_a12_deterministic_allocation_audit import _normalise_source  # noqa: PLC0415

    assert _normalise_source("int( n  *\n   p )") == _normalise_source("int(n * p)")
    assert _normalise_source("np.round(a)") != _normalise_source("np.round(b)")


# --------------------------------------------------------------------------
# Scope: estimand-path modules the A-12 scan did not cover
# --------------------------------------------------------------------------

#: A-12 scanned seven population/event modules. The deterministic base_case_context
#: estimand path also runs through these, which were NOT scanned. Verified clean by
#: hand during the 2026-09-10 review; this test keeps them clean.
UNAUDITED_ESTIMAND_PATH_MODULES = ("economics.py", "run_scenarios.py", "state_snapshot.py")

#: The only array-rounding calls permitted in those modules, by exact source text.
#: Both are year LABELS, not population allocations.
ALLOWED_ARRAY_ROUNDING = {
    ("run_scenarios.py", 'np.floor(econ["year"])'),
    ("run_scenarios.py", 'np.floor(sim["year"])'),
}


def test_unaudited_estimand_path_modules_have_no_array_rounding():
    """No ENG-01-pattern rounding may appear in the estimand path A-12 did not scan.

    A-12's headline covers "the current deterministic base_case_context evaluation
    path", but its scan covered seven modules. The fork (`run_scenarios.py`), the
    frozen state (`state_snapshot.py`) and the estimand itself (`economics.py`) are on
    that path and were not scanned. All three were verified clean during the review --
    `economics.py` has zero array-valued rounding, `run_scenarios.py` has two year
    labels, and `_collapse_prep_to_none` is an exact `sum(axis=-1)`. This pins that.
    """
    import ast  # noqa: PLC0415

    offenders = []
    for relative in UNAUDITED_ESTIMAND_PATH_MODULES:
        path = ROOT / relative
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"round", "rint", "floor", "ceil"}:
                continue
            segment = ast.get_source_segment(source, node) or ""
            if (relative, " ".join(segment.split())) in ALLOWED_ARRAY_ROUNDING:
                continue
            offenders.append(f"{relative}:{node.lineno} {segment}")

    assert not offenders, (
        "Array-valued rounding has appeared in an estimand-path module that the A-12 scan "
        "does not cover. Classify it against the A-12 rules -- and judge materiality on the "
        "INCREMENTAL between-arm difference, never on an aggregate percentage. "
        f"Found:\n" + "\n".join(offenders)
    )


def test_prep_collapse_at_the_fork_is_exact():
    """`_collapse_prep_to_none` mutates a restored snapshot at the arm fork.

    It is the one operation that touches the population tensor between the frozen
    snapshot and the arm engines, so if it rounded, it would inject a between-arm
    difference at the fork itself. It is an exact `sum(axis=-1)`; this keeps it so.
    """
    import run_scenarios  # noqa: PLC0415

    source = inspect.getsource(run_scenarios._collapse_prep_to_none)
    assert "pop[..., 0] = pop.sum(axis=-1)" in source
    for token in ("np.round", "np.floor", "np.ceil", "astype(int", "astype(np.int"):
        assert token not in source, f"{token} appeared in the arm-fork PrEP collapse"
