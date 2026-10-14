"""Focused tests for the unified matched deployment HTA harness.

These cover scenario construction, arm pairing, the deployment-family semantics
gate, the arithmetic identity gate and the reproduction gate.  They run no
simulation.
"""

from __future__ import annotations

import copy
import io
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from tools.run_matched_deployment_hta import (
    ADD_ON_LEVELS,
    BASE_SCENARIO,
    COMPLETED_SCENARIOS,
    DEPLOYMENT_FAMILY,
    DEPLOYMENT_SCENARIOS,
    EXPECTED_ARMS,
    LENA_SCENARIOS,
    ORAL_EXPANSION,
    SUBSTITUTION_LEVELS,
    build_plan,
    identity_gate,
    isr_decrement_per_person_year,
    pair_arms,
    reproduction_gate,
    select_deployment_scenarios,
    semantics_gate,
)

ROOT = Path(__file__).resolve().parents[1]
COMPLETED_RUN = (
    ROOT
    / "outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922"
    / "matched_hta_20260922/per_seed_economics.csv"
)


# ---------------------------------------------------------------------------
# Scenario matrix
# ---------------------------------------------------------------------------
def test_matrix_is_five_substitution_three_add_on_plus_two_context():
    families = [DEPLOYMENT_FAMILY[s] for s in DEPLOYMENT_SCENARIOS]
    assert families.count("lena_substitution") == 5 == len(SUBSTITUTION_LEVELS)
    assert families.count("lena_add_on") == 3 == len(ADD_ON_LEVELS)
    assert families.count("attribution_context") == 1
    assert families.count("oral_expansion_benchmark") == 1
    assert len(DEPLOYMENT_SCENARIOS) == 10
    # The eight lenacapavir deployment scenarios are five substitution plus
    # three add-on, never eight substitution levels.
    assert len(LENA_SCENARIOS) == 8


def test_first_six_scenarios_match_the_completed_substitution_run():
    assert tuple(DEPLOYMENT_SCENARIOS[:6]) == COMPLETED_SCENARIOS
    completed = pd.read_csv(COMPLETED_RUN)
    assert set(completed["scenario"].unique()) == set(COMPLETED_SCENARIOS)


def test_pair_arms_keeps_oral_soc_as_the_decision_comparator():
    assert pair_arms(BASE_SCENARIO) == ("no_prep", "oral_prep")
    assert pair_arms(ORAL_EXPANSION) == ("oral_prep", "oral_prep_expansion")
    for scenario in LENA_SCENARIOS:
        assert pair_arms(scenario) == ("oral_prep", "oral_prep_plus_lena")


def _fake_scenarios() -> list[dict]:
    return [
        {"name": name, "arm_order": list(arms)}
        for name, arms in ((n, EXPECTED_ARMS[n]) for n in DEPLOYMENT_SCENARIOS)
    ]


def test_select_deployment_scenarios_accepts_the_frozen_matrix():
    selected = select_deployment_scenarios(_fake_scenarios())
    assert [s["name"] for s in selected] == list(DEPLOYMENT_SCENARIOS)


def test_select_deployment_scenarios_rejects_a_missing_scenario():
    scenarios = [s for s in _fake_scenarios() if "add_on_1500" not in s["name"]]
    with pytest.raises(ValueError, match="missing"):
        select_deployment_scenarios(scenarios)


def test_select_deployment_scenarios_rejects_a_swapped_arm_order():
    scenarios = _fake_scenarios()
    for scenario in scenarios:
        if scenario["name"] == ORAL_EXPANSION:
            scenario["arm_order"] = ["oral_prep_expansion", "oral_prep"]
    with pytest.raises(ValueError, match="unexpected arm order"):
        select_deployment_scenarios(scenarios)


def test_select_deployment_scenarios_rejects_an_extra_scenario():
    scenarios = _fake_scenarios() + [
        {"name": "scenario_d_unapproved", "arm_order": ["oral_prep", "x"]}
    ]
    with pytest.raises(ValueError, match="outside the frozen matrix"):
        select_deployment_scenarios(scenarios)


def test_plan_counts_are_arithmetically_consistent():
    plan = build_plan(list(range(42, 57)))
    assert plan["n_seeds"] == 15
    assert plan["model_runs"] == 30
    assert plan["arms_per_model_run"] == 20
    assert plan["forward_arm_trajectories"] == 600
    assert plan["total_simulation_trajectories"] == 630
    assert plan["comparison_rows"] == 300


# ---------------------------------------------------------------------------
# Adverse-event convention
# ---------------------------------------------------------------------------
def test_isr_decrement_matches_the_configured_prevalence_weighted_value():
    costing = json.loads(
        (ROOT / "configs/hta_costing_config.json").read_text(encoding="utf-8")
    )
    assert isr_decrement_per_person_year(costing) == pytest.approx(0.01115, abs=1e-12)


def test_isr_decrement_refuses_state_machine_mode():
    costing = json.loads(
        (ROOT / "configs/hta_costing_config.json").read_text(encoding="utf-8")
    )
    costing = copy.deepcopy(costing)
    costing["adverse_events"]["mode"] = "state_machine"
    with pytest.raises(ValueError, match="prevalence"):
        isr_decrement_per_person_year(costing)


def test_isr_decrement_refuses_an_unmodelled_oral_adverse_event_item():
    """An oral-PrEP AE item would break the d_oral = 0 assumption silently."""
    costing = json.loads(
        (ROOT / "configs/hta_costing_config.json").read_text(encoding="utf-8")
    )
    costing = copy.deepcopy(costing)
    costing["adverse_events"]["items"].append(
        {
            "id": "oral_prep_renal_ae",
            "arm": "oral_prep",
            "billing_moment": "ongoing",
            "prevalence": {"value": 0.02},
            "utility_decrement": {"value": -0.01},
        }
    )
    with pytest.raises(ValueError, match="non-lenacapavir"):
        isr_decrement_per_person_year(costing)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------
def _synthetic_rows(prep_change: dict[str, float] | None = None) -> pd.DataFrame:
    prep_change = prep_change or {}
    records = []
    for structure in ("legacy", "candidate"):
        for seed in (42, 43):
            for scenario in DEPLOYMENT_SCENARIOS:
                family = DEPLOYMENT_FAMILY[scenario]
                default = {
                    "attribution_context": 1.0,
                    "lena_substitution": 0.0004,
                    "lena_add_on": 0.05,
                    "oral_expansion_benchmark": 0.8,
                }[family]
                relative = prep_change.get(scenario, default)
                delta_cost, delta_qaly = 1_000.0 + seed, -20.0 + seed
                lena_py = 1_000.0 if scenario in LENA_SCENARIOS else 0.0
                delta_ae = -0.01115 * lena_py
                row = {
                    "structure": structure,
                    "seed": seed,
                    "scenario": scenario,
                    "deployment_family": family,
                    "delta_cost": delta_cost,
                    "delta_qaly": delta_qaly,
                    "delta_ae_qaly_disc": delta_ae,
                    "delta_qaly_excluding_ae": delta_qaly - delta_ae,
                    "delta_qaly_no_isr": delta_qaly - delta_ae,
                    "ae_identity_max_abs_error_disc": 0.0,
                    "total_prep_person_years_relative_change": relative,
                    "infections_averted": 3.0,
                }
                for wtp in (20_000.0, 50_000.0, 80_000.0):
                    slug = f"{int(wtp / 1000)}k"
                    row[f"nmb_{slug}"] = wtp * delta_qaly - delta_cost
                    row[f"nmb_no_isr_{slug}"] = (
                        wtp * row["delta_qaly_no_isr"] - delta_cost
                    )
                records.append(row)
    return pd.DataFrame(records)


def test_semantics_gate_accepts_a_well_formed_matrix():
    frame = semantics_gate(_synthetic_rows())
    assert frame["passes"].all()
    assert set(frame["scenario"]) == set(DEPLOYMENT_SCENARIOS) - {BASE_SCENARIO}


def test_semantics_gate_fails_add_on_that_does_not_expand_coverage():
    rows = _synthetic_rows({"scenario_b_lena_add_on__add_on_500": 0.0})
    with pytest.raises(AssertionError, match="semantics gate failed"):
        semantics_gate(rows)


def test_semantics_gate_fails_substitution_that_changes_total_prep_stock():
    rows = _synthetic_rows({"scenario_a_lena_substitution__switch_50": 0.25})
    with pytest.raises(AssertionError, match="semantics gate failed"):
        semantics_gate(rows)


def test_identity_gate_accepts_consistent_rows_and_rejects_a_broken_nmb():
    frame = identity_gate(_synthetic_rows())
    assert frame["passes"].all()
    rows = _synthetic_rows()
    rows.loc[0, "nmb_80k"] = rows.loc[0, "nmb_80k"] + 1_000.0
    with pytest.raises(AssertionError, match="identity gate failed"):
        identity_gate(rows)


def test_identity_gate_rejects_a_broken_ae_reconstruction():
    rows = _synthetic_rows()
    rows.loc[1, "delta_qaly_excluding_ae"] = rows.loc[1, "delta_qaly_excluding_ae"] + 5.0
    with pytest.raises(AssertionError, match="identity gate failed"):
        identity_gate(rows)


def test_reproduction_gate_passes_against_the_completed_run():
    completed = pd.read_csv(COMPLETED_RUN)
    frame = reproduction_gate(completed, COMPLETED_RUN)
    assert len(frame) == len(completed) * 3
    assert frame["passes"].all()


def test_reproduction_gate_fails_on_any_drift():
    completed = pd.read_csv(COMPLETED_RUN)
    perturbed = completed.copy()
    perturbed.loc[0, "delta_qaly"] = perturbed.loc[0, "delta_qaly"] + 1e-6
    with pytest.raises(AssertionError, match="reproduction gate failed"):
        reproduction_gate(perturbed, COMPLETED_RUN)


def test_reproduction_gate_ignores_the_new_deployment_scenarios():
    """Add-on and benchmark rows are new; the gate must not demand them."""
    completed = pd.read_csv(COMPLETED_RUN)
    extra = completed.head(1).copy()
    extra["scenario"] = "scenario_b_lena_add_on__add_on_500"
    extra["delta_qaly"] = math.nan
    frame = reproduction_gate(pd.concat([completed, extra], ignore_index=True), COMPLETED_RUN)
    assert frame["passes"].all()


# ---------------------------------------------------------------------------
# Preflight and end-to-end row extraction
#
# Regression cover for the 2026-09-23 failure: extract_rows read
# hta_config["study_defaults"], a key that exists on configs/hta_runtime_config.json
# but NOT on the composed config MultiScenarioRunner actually holds. --dry-run
# never constructed a runner or called extract_rows, so the mistake only surfaced
# inside a worker after the simulation had run.
# ---------------------------------------------------------------------------
import types  # noqa: E402

from hta_config_loader import load_hta_config  # noqa: E402
from scenario_comparator import ScenarioResult  # noqa: E402

from tools.run_matched_deployment_hta import (  # noqa: E402
    REQUIRED_CONFIG_PATHS,
    extract_rows,
    preflight_config_access,
)

COMPOSED = load_hta_config(str(ROOT / "configs/hta_runtime_config.json"))


def test_composed_config_has_no_study_defaults_key():
    """The exact shape that caused the failure, pinned so it cannot recur silently."""
    assert "study_defaults" in json.loads(
        (ROOT / "configs/hta_runtime_config.json").read_text(encoding="utf-8")
    )
    assert "study_defaults" not in COMPOSED
    assert "time_horizon" in COMPOSED


def test_preflight_passes_on_the_real_composed_config():
    checked = preflight_config_access(COMPOSED, {"run_control": {"step_size": 3}})
    assert "hta:time_horizon.evaluation_years" in checked
    assert len(checked) == len(REQUIRED_CONFIG_PATHS)


def test_preflight_names_the_missing_path():
    broken = copy.deepcopy(COMPOSED)
    del broken["time_horizon"]["evaluation_years"]
    with pytest.raises(KeyError, match="time_horizon.evaluation_years"):
        preflight_config_access(broken, {"run_control": {"step_size": 3}})


def test_preflight_would_have_caught_a_runtime_only_key():
    """Adding a runtime-only path to the required list must fail against the composed config."""
    import tools.run_matched_deployment_hta as mod

    original = mod.REQUIRED_CONFIG_PATHS
    mod.REQUIRED_CONFIG_PATHS = original + (("hta", ("study_defaults", "evaluation_years")),)
    try:
        with pytest.raises(KeyError, match="study_defaults"):
            mod.preflight_config_access(COMPOSED, {"run_control": {"step_size": 3}})
    finally:
        mod.REQUIRED_CONFIG_PATHS = original


def _fake_arm(name: str, *, lena_py: float, oral_py: float, cost: float,
              qaly: float, infections: int) -> ScenarioResult:
    """A ScenarioResult shaped like a real one, with two timesteps and one sim record."""
    isr = 0.01115
    steps = [
        {
            "step": i,
            "year": 2025 + i,
            "step_qaly_susceptible_lenacapavir_disc": lena_py / 2,
            "step_qaly_susceptible_lenacapavir_undisc": lena_py / 2,
            "step_qaly_susceptible_lenacapavir_waning_disc": 0.0,
            "step_qaly_susceptible_lenacapavir_waning_undisc": 0.0,
            "step_qaly_susceptible_oral_prep_disc": oral_py / 2,
            "step_qaly_susceptible_oral_prep_undisc": oral_py / 2,
            "step_qaly_utility_decrement_adverse_events_disc": -isr * lena_py / 2,
            "step_qaly_utility_decrement_adverse_events_undisc": -isr * lena_py / 2,
        }
        for i in range(2)
    ]
    sim = [{"year": 2030, "new_infections": infections, "msm_on_lena": lena_py,
            "msm_on_oral": oral_py, "hiv_deaths": 1.0}]
    return ScenarioResult(
        name=name,
        analysis_scenario="fake",
        total_qalys_disc=qaly,
        total_qalys_undisc=qaly,
        total_costs_disc=cost,
        total_costs_undisc=cost,
        cost_breakdown_disc={"prep_drug_lenacapavir": 1e8 if lena_py else 0.0,
                             "prep_drug_oral": 1e7, "drug": 2e8, "hiv_care": 1e8},
        cumul_new_infections=infections,
        timestep_records=steps,
        simulation_records=sim,
        total_costs_disc_societal=cost,
        total_societal_additions_disc=0.0,
        societal_breakdown_disc={},
    )


def _fake_runner():
    results = {}
    for scenario in DEPLOYMENT_SCENARIOS:
        comparator, intervention = pair_arms(scenario)
        has_lena = scenario in LENA_SCENARIOS
        results[scenario] = {
            comparator: _fake_arm(comparator, lena_py=0.0, oral_py=1000.0,
                                  cost=1e9, qaly=1e6, infections=500),
            intervention: _fake_arm(intervention,
                                    lena_py=800.0 if has_lena else 0.0,
                                    oral_py=300.0 if has_lena else 1400.0,
                                    cost=1.2e9, qaly=1e6 + 50, infections=480),
        }
    return types.SimpleNamespace(
        hta_config=COMPOSED,
        base_config={"run_control": {"step_size": 3}},
        freeze_year=2025,
        eval_years=40,
        results=results,
    )


def test_extract_rows_runs_end_to_end_on_a_fake_runner():
    """Exercises the whole extractor without simulating. This is the gap that let
    the study_defaults KeyError reach a worker."""
    rows = extract_rows(_fake_runner(), "legacy", 42, 0.01115)
    assert len(rows) == len(DEPLOYMENT_SCENARIOS)
    frame = pd.DataFrame(rows)
    assert (frame["freeze_year"] == 2025).all()
    assert (frame["horizon_years"] == 40).all()
    # The AE reconstruction must be exact on synthetic data too.
    assert frame["ae_identity_max_abs_error_disc"].max() < 1e-9
    identity_gate(frame)
    # Price roots exist for the eight lenacapavir scenarios and not the other two.
    has_root = frame["lena_product_cost"].notna()
    assert set(frame.loc[has_root, "scenario"]) == set(LENA_SCENARIOS)


def test_extract_rows_emits_every_ae_reweighting_field():
    """The fields the package promises for the symmetric no-ISR sensitivity must
    actually be produced by the extractor."""
    rows = extract_rows(_fake_runner(), "legacy", 42, 0.01115)
    produced = set(rows[0])
    promised = set(
        pd.read_csv(
            ROOT / "outputs/abstract_evidence_20260923/ae_reweighting_fields.csv"
        )["field"]
    )
    assert promised <= produced, f"missing from extractor output: {sorted(promised - produced)}"


# ---------------------------------------------------------------------------
# Reproduction-gate float handling
#
# Regression cover for the 2026-09-23 gate failure. The simulation reproduced the
# completed run bit-for-bit, but the gate compared exact in-memory float64 values
# against pandas' DEFAULT CSV parser, which is fast and not correctly rounded: on
# the completed run's own file it misparses 20 of 180 delta_cost values by up to
# one ULP (4.77e-07 absolute, 2.0e-16 relative). With a purely absolute 1e-9
# tolerance on a EUR 2.5e9 quantity, that reads as 20 failures.
# ---------------------------------------------------------------------------
def test_default_csv_parser_is_not_correctly_rounded():
    """Pin the pandas behaviour that caused the failure, so it cannot surprise us again."""
    text = "x\n235250057.49680042\n"
    exact = float("235250057.49680042")
    assert text.split()[1] == repr(exact), "the CSV text is the exact repr"
    fast = pd.read_csv(io.StringIO(text))["x"].to_numpy()[0]
    roundtrip = pd.read_csv(io.StringIO(text), float_precision="round_trip")["x"].to_numpy()[0]
    assert fast != exact, "default parser is expected to be off by ~1 ULP here"
    assert roundtrip == exact, "round_trip parser must be bit-exact"


def test_reference_file_has_values_the_default_parser_misreads():
    fast = pd.read_csv(COMPLETED_RUN)
    exact = pd.read_csv(COMPLETED_RUN, float_precision="round_trip")
    misparsed = int(((fast["delta_cost"] - exact["delta_cost"]).abs() > 0).sum())
    assert misparsed > 0, (
        "if this ever becomes 0 the pandas parser changed; the gate's round_trip "
        "read is still correct, but this regression test no longer proves anything"
    )
    worst_relative = (
        (fast["delta_cost"] - exact["delta_cost"]).abs() / exact["delta_cost"].abs()
    ).max()
    assert worst_relative < 1e-15, "misparse must be representation noise, not a real difference"


def test_gate_passes_on_exactly_reproduced_rows():
    """The exact scenario that failed: gate exact rows against the reference file."""
    rows = pd.read_csv(COMPLETED_RUN, float_precision="round_trip")
    frame = reproduction_gate(rows, COMPLETED_RUN)
    assert frame["passes"].all()
    assert frame["ulp_distance"].max() == 0.0


def test_gate_still_fails_on_a_difference_that_matters():
    """Scale-aware does not mean slack: a 1e-6 relative change must still fail."""
    rows = pd.read_csv(COMPLETED_RUN, float_precision="round_trip")
    rows.loc[0, "delta_cost"] = rows.loc[0, "delta_cost"] * (1 + 1e-6)
    with pytest.raises(AssertionError, match="reproduction gate failed"):
        reproduction_gate(rows, COMPLETED_RUN)


def test_gate_requires_exact_equality_for_integer_infection_counts():
    rows = pd.read_csv(COMPLETED_RUN, float_precision="round_trip")
    rows.loc[0, "infections_averted"] = rows.loc[0, "infections_averted"] + 1e-9
    with pytest.raises(AssertionError, match="reproduction gate failed"):
        reproduction_gate(rows, COMPLETED_RUN)


def test_gate_reports_relative_difference_and_ulp_distance():
    rows = pd.read_csv(COMPLETED_RUN, float_precision="round_trip")
    frame = reproduction_gate(rows, COMPLETED_RUN)
    for column in ("abs_difference", "relative_difference", "ulp_distance"):
        assert column in frame.columns


def test_the_original_bug_reproduces_exactly_twenty_delta_cost_failures():
    """Quantitative record of the 2026-09-23 failure: exact values vs a
    default-parsed reference, judged by an absolute-only 1e-9 tolerance."""
    exact = pd.read_csv(COMPLETED_RUN, float_precision="round_trip")
    fast = pd.read_csv(COMPLETED_RUN)  # what the old gate used as its reference
    failures = {}
    for metric in ("delta_cost", "delta_qaly", "infections_averted"):
        failures[metric] = int(((exact[metric] - fast[metric]).abs() > 1e-9).sum())
    assert failures["delta_cost"] == 20, failures
    assert failures["delta_qaly"] == 0, failures
    assert failures["infections_averted"] == 0, failures
    worst = (exact["delta_cost"] - fast["delta_cost"]).abs().max()
    assert abs(worst - 4.76837158203125e-07) < 1e-15, worst
