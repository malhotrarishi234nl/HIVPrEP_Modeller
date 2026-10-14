"""Tests for the A-15 runner (`tools/run_a15_a9_allocation_hypothesis.py`).

A-15 is a diagnostic: it drives the existing A-7 grid runner with the existing
A-14 allocation harness.  The new code is therefore thin, and these tests cover
exactly the thin parts — the pieces that, if wrong, would silently produce a
believable but incorrect grid:

  * the monotonicity verdict (the acceptance criterion is a fact, so the
    function that decides it must be pinned in both directions),
  * exact-match reporting against the A-9 trigger sequence (a near-miss must
    not be reported as a reproduction),
  * the A-9 trigger constant agreeing with the A-7 artefact on disk,
  * the arm-tagging wrapper, which attributes stage identities to a switch
    level — a mis-ordered tag would mislabel every identity row,
  * run-mode statelessness, so a stochastic payload cannot leak its mode into a
    deterministic payload sharing the same worker process,
  * the engine subclass leaving level-0 allocation bit-identical to production
    while level 1 preserves per-stage expectations.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_scenarios import MultiScenarioRunner  # noqa: E402
from stochastic_allocation import deterministic_allocate_events  # noqa: E402
from tools import run_a15_a9_allocation_hypothesis as a15  # noqa: E402
from tools.run_a14_allocation_granularity_sweep import A14AllocationEngine  # noqa: E402


# ── monotonicity ───────────────────────────────────────────────────────────


def test_a9_trigger_sequence_is_not_monotone_and_has_two_descents():
    report = a15.monotonicity_report([38, 7, 34, 113, 96])
    assert report["monotone_non_decreasing"] is False
    assert report["n_descents"] == 2
    assert report["descent_positions"] == [0, 3]
    assert report["largest_drop"] == pytest.approx(31.0)
    assert report["total_drop"] == pytest.approx(48.0)


def test_monotone_sequence_reports_no_descents():
    report = a15.monotonicity_report([10, 10, 25, 40, 40])
    assert report["monotone_non_decreasing"] is True
    assert report["n_descents"] == 0
    assert report["total_drop"] == 0.0


def test_single_tiny_descent_is_still_a_descent():
    # "Looks better" is not a verdict: any fall breaks monotonicity.
    report = a15.monotonicity_report([10, 20, 19.999, 40, 50])
    assert report["monotone_non_decreasing"] is False
    assert report["n_descents"] == 1


# ── exact-match reporting ──────────────────────────────────────────────────


def test_exact_match_rows_flag_a_near_miss_as_a_mismatch():
    rows = a15.exact_match_rows({10: 38.0, 25: 7.0}, {10: 38.0, 25: 8.0})
    by_switch = {row["switch_percent"]: row for row in rows}
    assert by_switch[10]["exact_match"] is True
    assert by_switch[25]["exact_match"] is False
    assert by_switch[25]["difference"] == pytest.approx(-1.0)


def test_exact_match_rows_report_missing_observations_as_nan():
    rows = a15.exact_match_rows({10: 38.0}, {10: 38.0, 25: 7.0})
    missing = [row for row in rows if row["switch_percent"] == 25][0]
    assert np.isnan(missing["observed"])
    assert missing["exact_match"] is False


def test_a9_trigger_constant_matches_the_a7_artifact_on_disk():
    """The constant A-15 reproduces against must come from A-7, not from A-15."""
    import pandas as pd

    path = a15.A7_REFERENCE_DIR / "scenario_decomposition.csv"
    if not path.exists():  # pragma: no cover - artifact retention guard
        pytest.skip(f"A-7 reference artifact absent: {path}")
    frame = pd.read_csv(path)
    observed = {
        int(row["switch_percent"]): float(row["infections_averted"])
        for _, row in frame.iterrows()
    }
    assert observed == a15.A9_TRIGGER_INFECTIONS_AVERTED
    switch_100 = frame.loc[frame["switch_percent"] == 100, "delta_person_years"].iloc[0]
    assert float(switch_100) == pytest.approx(
        a15.A9_TRIGGER_SWITCH_100_PERSON_YEARS
    )


# ── arm tagging ────────────────────────────────────────────────────────────


def test_expected_arm_tags_are_scenario_major_arm_minor():
    tags = a15.expected_arm_tags()
    assert len(tags) == 10
    assert tags[0] == ("scenario_a_lena_substitution__switch_10", "oral_prep")
    assert tags[1] == (
        "scenario_a_lena_substitution__switch_10",
        "oral_prep_plus_lena",
    )
    assert tags[-1] == (
        "scenario_a_lena_substitution__switch_100",
        "oral_prep_plus_lena",
    )


def test_arm_tagging_wrapper_records_scenario_and_arm(monkeypatch):
    calls = []

    def stub(self, arm_name, analysis_scenario, param_overrides=None):
        calls.append((arm_name, analysis_scenario["name"], param_overrides))
        return {"built": arm_name}

    monkeypatch.setattr(MultiScenarioRunner, "_build_arm_config", stub, raising=True)
    a15.patch_arm_tagging()
    a15.patch_arm_tagging()  # idempotent: must not double-wrap

    runner = SimpleNamespace()
    result = MultiScenarioRunner._build_arm_config(
        runner, "oral_prep_plus_lena", {"name": "scenario_a_lena_substitution__switch_75"}
    )
    assert result == {"built": "oral_prep_plus_lena"}
    assert calls == [
        ("oral_prep_plus_lena", "scenario_a_lena_substitution__switch_75", None)
    ]
    assert a15.CURRENT_TAG == {
        "scenario": "scenario_a_lena_substitution__switch_75",
        "arm": "oral_prep_plus_lena",
    }
    assert a15.RUNNER_REF["runner"] is runner


# ── run mode ───────────────────────────────────────────────────────────────


def test_apply_run_mode_is_stateless_across_successive_payloads():
    config = {
        "run_control": {
            "deterministic_mode": {"value": True, "source": "provenance kept"},
            "seed": {"value": 1},
        }
    }
    a15.apply_run_mode(config, {"stochastic": True, "seed": 43})
    assert config["run_control"]["deterministic_mode"]["value"] is False
    assert config["simulation_controls"]["deterministic_mode"] == {"value": False}
    assert config["run_control"]["seed"] == {"value": 43}
    # The same process then runs a deterministic payload: it must be deterministic.
    a15.apply_run_mode(config, {"stochastic": False, "seed": 42})
    assert config["run_control"]["deterministic_mode"]["value"] is True
    assert config["simulation_controls"]["deterministic_mode"] == {"value": True}
    assert config["run_control"]["seed"] == {"value": 42}
    assert config["run_control"]["deterministic_mode"]["source"] == "provenance kept"


# ── engine subclass ────────────────────────────────────────────────────────


def _bare_engine(level: int):
    """An A-15 engine with only the attributes `_draw_events_for` needs.

    Constructing a real `SimulationEngineV3` would run config validation and
    population initialisation; the allocation path under test depends on none
    of it.
    """
    engine = object.__new__(a15.make_engine_class(level))
    engine.deterministic = True
    engine._a14_level = level
    engine._a14_stage_totals = {}
    engine._a14_axis_log = {}
    return engine


def test_make_engine_class_carries_the_level_and_the_a14_harness():
    cls = a15.make_engine_class(3)
    assert cls.A14_LEVEL == 3
    assert issubclass(cls, A14AllocationEngine)


def test_level0_engine_is_bit_identical_to_production_allocation():
    rng = np.random.default_rng(20260911)
    pool = rng.integers(0, 400, size=(4, 2, 3, 3, 1, 2, 1)).astype(np.int64)
    engine = _bare_engine(0)
    for p in (0.0, 0.013, 0.17, 0.5, 1.0):
        observed = engine._draw_events_for("art_initiation", pool, p)
        np.testing.assert_array_equal(observed, deterministic_allocate_events(pool, p))


def test_level1_engine_preserves_each_hiv_stage_total_where_level0_does_not():
    # Rare AIDS stratum (axis 3, index 2) against a common Chronic stratum.
    pool = np.zeros((6, 2, 3, 3, 1, 2, 1), dtype=np.int64)
    pool[:, :, :, 1, 0, 0, 0] = 900  # Chronic
    pool[:, :, :, 2, 0, 0, 0] = 7  # AIDS
    p = 0.137
    expected_aids = pool[:, :, :, 2].sum() * p

    level0 = _bare_engine(0)._draw_events_for("art_initiation", pool, p)
    level1 = _bare_engine(1)._draw_events_for("art_initiation", pool, p)

    assert level0.sum() == level1.sum() or abs(int(level0.sum()) - int(level1.sum())) <= 3
    level0_aids_error = abs(int(level0[:, :, :, 2].sum()) - expected_aids)
    level1_aids_error = abs(int(level1[:, :, :, 2].sum()) - expected_aids)
    assert level1_aids_error < level0_aids_error
    assert level1_aids_error <= 1.0


def test_engine_records_stage_identities_for_the_diagnosis_call_sites():
    pool = np.zeros((6, 2, 3, 3, 1, 2, 1), dtype=np.int64)
    pool[:, :, :, 1, 0, 0, 0] = 500
    pool[:, :, :, 2, 0, 0, 0] = 9
    engine = _bare_engine(1)
    engine._draw_events_for("diagnosis_msm", pool, 0.2)
    assert set(engine._a14_stage_totals) == {
        "diagnosis_acute",
        "diagnosis_chronic",
        "diagnosis_aids",
    }
    aids = engine._a14_stage_totals["diagnosis_aids"]
    assert aids["pool"] == pytest.approx(float(pool[:, :, :, 2].sum()))
    assert aids["expected"] == pytest.approx(aids["pool"] * 0.2)


# ── grid assembly ──────────────────────────────────────────────────────────


def test_grid_rows_convert_hiv_death_flows_into_deaths_averted():
    result = {
        "allocation_level": "0",
        "level": 0,
        "seed": 42,
        "stochastic": False,
        "decomposition": [
            {
                "switch_percent": 100,
                "infections_averted": 96.0,
                "delta_qaly": -1827.4,
                "delta_person_years": -33.5,
                "delta_discounted_person_years": -32.0,
                "final_total_population_delta": 8.0,
            }
        ],
        "flows": [
            {
                "switch_percent": 100,
                "flow": flow,
                "delta_intervention_minus_reference": value,
            }
            for flow, value in (
                ("hiv_deaths", -2.0),
                ("aids_deaths", -2.0),
                ("new_aids_cases", -17.0),
                ("new_diagnoses", -82.0),
                ("art_initiations", -90.0),
                ("background_deaths", -4.0),
            )
        ],
    }
    rows = a15._grid_rows(result)
    assert len(rows) == 1
    row = rows[0]
    # The flow is intervention − reference, so deaths averted is its negation.
    assert row["hiv_deaths_averted"] == pytest.approx(2.0)
    assert row["delta_new_diagnoses"] == pytest.approx(-82.0)
    assert row["infections_averted"] == pytest.approx(96.0)


# ── scope of the allocation dial ───────────────────────────────────────────


def test_transmission_is_outside_the_allocation_dial():
    """No granularity level can re-allocate infection events directly.

    A-15 reports that level 1 moves `infections_averted` only through the
    downstream cascade (stage composition -> ART coverage -> onward
    transmission).  That claim rests on transmission owning its own allocator
    and never routing through the engine's `_draw_events_for`, which is what
    this test pins.
    """
    import re

    from tools.allocation_granularity import (
        CALL_SITE_AXES,
        CALL_SITE_AXES_BY_FAMILY,
    )

    transmission_src = (ROOT / "transmission_corrected.py").read_text(encoding="utf-8")
    assert "_draw_events_for" not in transmission_src
    assert "_deterministic_allocate_expected_infections" in transmission_src

    engine_src = (ROOT / "run_v3.py").read_text(encoding="utf-8")
    process_ids = set(
        re.findall(r"_draw_events_for\(\s*\(?\s*[\"']([a-z0-9_]+)[\"']", engine_src)
    )
    assert process_ids, "no _draw_events_for call sites found in run_v3.py"
    assert not any(
        "infect" in name or "transmission" in name for name in process_ids
    ), process_ids

    mapped = set(str(key) for key in CALL_SITE_AXES) | set(CALL_SITE_AXES_BY_FAMILY)
    assert not any("infect" in name or "transmission" in name for name in mapped)
    # Every engine-routed call site the dial can see must be declared, or the
    # harness would silently apply level 0 to it.
    assert process_ids <= mapped, process_ids - mapped
