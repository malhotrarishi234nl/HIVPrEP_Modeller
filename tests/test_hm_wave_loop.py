"""Tests for the history-matching wave loop (HM-4).

The loop is exercised against a synthetic simulator so the mechanics -- design
generation, NROY restriction, stopping rule, manifest -- are tested without
running the real model.  Nothing here validates the epidemiology; it validates
that the loop does what spec section 5 says it does.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import hm_implausibility as hm
import hm_target_register as tr
import run_history_matching as rhm


# --------------------------------------------------------------------------
# Wave plan
# --------------------------------------------------------------------------

def test_wave_plan_template_parses_and_declares_a_stopping_rule():
    waves, stopping, plan = rhm.load_wave_plan(Path(__file__).resolve().parent.parent / "configs" / "hm_wave_plan_template.json")
    assert len(waves) >= 1
    assert stopping.max_waves > 0
    assert stopping.min_nroy_members >= 10          # spec section 10 escalation floor
    assert 0.0 < stopping.min_volume_shrinkage < 1.0
    # Each wave must name its targets up front.
    assert all(wave.target_series for wave in waves)
    # No excluded or tier-2 series may be planned as a target.
    planned = {name for wave in waves for name in wave.target_series}
    assert "shm_new_diagnoses_non_msm" not in planned
    assert not any(name.startswith("unaids_") for name in planned)
    assert plan["status"].startswith("TEMPLATE")


def test_wave_plan_with_no_waves_is_rejected(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"stopping_rule": {"max_waves": 1, "min_volume_shrinkage": 0.1, "min_nroy_members": 10}, "waves": []}))
    with pytest.raises(ValueError, match="declares no waves"):
        rhm.load_wave_plan(path)


# --------------------------------------------------------------------------
# Stopping rule
# --------------------------------------------------------------------------

def _rule(**kwargs):
    return rhm.StoppingRule(**{"max_waves": 5, "min_volume_shrinkage": 0.10, "min_nroy_members": 10, **kwargs})


def test_empty_nroy_stops_the_loop_and_says_it_is_a_result():
    stop, reason = _rule().should_stop([{"n_nroy": 0, "frac_candidates": 0.0}])
    assert stop
    assert "RESULT, not a failure" in reason
    assert "Do NOT widen" in reason


def test_tiny_nroy_escalates_rather_than_continuing():
    stop, reason = _rule().should_stop([{"n_nroy": 4, "frac_candidates": 0.001}])
    assert stop and "ESCALATE" in reason


def test_loop_continues_while_the_set_is_still_shrinking():
    history = [{"n_nroy": 1000, "frac_candidates": 0.25}, {"n_nroy": 400, "frac_candidates": 0.10}]
    assert _rule().should_stop(history)[0] is False


def test_loop_stops_when_shrinkage_falls_below_the_pre_specified_threshold():
    history = [{"n_nroy": 1000, "frac_candidates": 0.250}, {"n_nroy": 990, "frac_candidates": 0.248}]
    stop, reason = _rule().should_stop(history)
    assert stop and "min_volume_shrinkage" in reason


def test_max_waves_is_a_hard_cap():
    history = [{"n_nroy": 500, "frac_candidates": 0.5 / (i + 1)} for i in range(2)]
    stop, reason = _rule(max_waves=2).should_stop(history)
    assert stop and "max_waves" in reason


# --------------------------------------------------------------------------
# Design generation and NROY restriction
# --------------------------------------------------------------------------

SPACE = {
    "parameters": [
        {"name": "a", "min": 0.0, "max": 10.0, "distribution": "uniform"},
        {"name": "b", "min": -1.0, "max": 1.0, "distribution": "uniform"},
    ]
}


def _fake_draws(parameters, n, seed):
    rng = np.random.default_rng(seed)
    return [
        {p["name"]: float(rng.uniform(p["min"], p["max"])) for p in parameters}
        for _ in range(n)
    ]


def test_design_stays_inside_the_declared_box():
    design = rhm.design_over_box(SPACE, 200, seed=1, draw_fn=_fake_draws)
    assert len(design) == 200
    assert design["a"].between(0.0, 10.0).all()
    assert design["b"].between(-1.0, 1.0).all()


def test_restrict_space_narrows_to_the_retained_range_and_never_widens_the_prior():
    retained = pd.DataFrame({"a": [4.0, 5.0, 6.0], "b": [0.1, 0.2, 0.3]})
    restricted = rhm.restrict_space_to_nroy(SPACE, retained)
    bounds = {p["name"]: (p["min"], p["max"]) for p in restricted["parameters"]}
    assert bounds["a"] == (4.0, 6.0)
    assert bounds["b"] == pytest.approx((0.1, 0.3))
    # The original space is untouched -- restriction must not mutate its input.
    assert SPACE["parameters"][0]["min"] == 0.0


def test_restrict_space_padding_is_clipped_to_the_prior_box():
    retained = pd.DataFrame({"a": [0.0, 10.0], "b": [-1.0, 1.0]})
    restricted = rhm.restrict_space_to_nroy(SPACE, retained, pad_fraction=0.5)
    bounds = {p["name"]: (p["min"], p["max"]) for p in restricted["parameters"]}
    assert bounds["a"] == (0.0, 10.0)
    assert bounds["b"] == (-1.0, 1.0)


def test_restrict_space_handles_a_parameter_pinned_to_one_value():
    retained = pd.DataFrame({"a": [3.0, 3.0], "b": [0.0, 0.5]})
    restricted = rhm.restrict_space_to_nroy(SPACE, retained)
    bounds = {p["name"]: (p["min"], p["max"]) for p in restricted["parameters"]}
    assert bounds["a"][1] > bounds["a"][0]


def test_restrict_space_rejects_an_empty_nroy():
    with pytest.raises(ValueError, match="empty NROY"):
        rhm.restrict_space_to_nroy(SPACE, pd.DataFrame(columns=["a", "b"]))


def test_thinning_only_ever_removes_proposals():
    retained = pd.DataFrame({"a": [5.0, 5.1, 4.9], "b": [0.0, 0.01, -0.01]})
    proposals = pd.DataFrame({"a": [5.0, 9.9, 5.05], "b": [0.0, 0.99, 0.0]})
    bounds = {"a": (0.0, 10.0), "b": (-1.0, 1.0)}
    thinned = rhm.thin_by_proximity(proposals, retained, bounds)
    assert len(thinned) <= len(proposals)
    assert set(thinned.index).issubset(set(proposals.index))
    assert 1 not in thinned.index          # the far-away proposal is dropped


def test_thinning_is_a_no_op_when_there_is_nothing_to_compare_against():
    proposals = pd.DataFrame({"a": [1.0], "b": [0.0]})
    empty = pd.DataFrame(columns=["a", "b"])
    assert len(rhm.thin_by_proximity(proposals, empty, {"a": (0.0, 10.0)})) == 1


# --------------------------------------------------------------------------
# Scoring a wave against the register
# --------------------------------------------------------------------------

WORKBOOK = (Path(__file__).resolve().parent.parent / "../data/raw/validation/DiseaseData.xlsx").resolve()
needs_workbook = pytest.mark.skipif(not WORKBOOK.exists(), reason="disease workbook not available")


@needs_workbook
def test_score_wave_refuses_cross_validation_series_as_targets():
    register = tr.load_register()
    evaluated = pd.DataFrame({"design_id": [0, 1], "PLHIV_2023": [1.0, 2.0]})
    with pytest.raises(ValueError, match="non-calibration series"):
        rhm.score_wave(evaluated, register, ["unaids_plhiv_all_ages"])


@needs_workbook
def test_score_wave_refuses_the_excluded_non_msm_series():
    register = tr.load_register()
    evaluated = pd.DataFrame({"design_id": [0, 1], "new_diagnoses_2024": [1.0, 2.0]})
    with pytest.raises(ValueError, match="non-calibration series"):
        rhm.score_wave(evaluated, register, ["shm_new_diagnoses_non_msm"])


@needs_workbook
def test_score_wave_refuses_an_unknown_series():
    register = tr.load_register()
    with pytest.raises(ValueError, match="absent from the register"):
        rhm.score_wave(pd.DataFrame({"design_id": [0]}), register, ["not_a_series"])


@needs_workbook
def test_score_wave_produces_an_nroy_mask_and_diagnostics():
    register = tr.load_register()
    years = list(range(2020, 2025))
    rng = np.random.default_rng(0)
    frame = pd.DataFrame({"design_id": range(50)})
    for year in years:
        z = next(t.z for t in register.targets if t.series_name == "shm_new_diagnoses_msm" and t.year == year)
        frame[f"new_diagnoses_msm_{year}"] = z + rng.normal(0.0, 50.0, 50)
    scored = rhm.score_wave(frame, register, ["shm_new_diagnoses_msm"], scored_years=years)
    assert scored["mask"].shape == (50,)
    assert len(scored["targets"]) == len(years)
    assert set(scored["per_target"]["series_name"]) == {"shm_new_diagnoses_msm"}
    assert not scored["budget_table"].empty
    # Every tier-1 budget here is V_disc-only: V_obs is incomplete and V_ensemble unavailable.
    assert (scored["budget_table"]["v_code_variance"] == 0.0).all()


# --------------------------------------------------------------------------
# End-to-end loop against a synthetic simulator
# --------------------------------------------------------------------------

@needs_workbook
def test_wave_loop_end_to_end_with_a_synthetic_simulator(tmp_path):
    """A toy simulator whose output depends on one parameter, so NROY must shrink."""
    register = tr.load_register()
    years = list(range(2020, 2025))
    z_by_year = {
        year: next(t.z for t in register.targets if t.series_name == "shm_new_diagnoses_msm" and t.year == year)
        for year in years
    }

    def fake_evaluate(design, base_config, calib_space, *, seed, workers=1, run_settings=None):
        frame = design.copy()
        frame.insert(0, "design_id", range(len(frame)))
        # Output tracks parameter 'a': only a near 5 reproduces the targets.
        offset = (frame["a"] - 5.0) * 200.0
        for year in years:
            frame[f"new_diagnoses_msm_{year}"] = z_by_year[year] + offset
        frame["error"] = ""
        return frame

    waves = [
        rhm.WaveSpec(index=1, n_design=120, seed=1, target_series=["shm_new_diagnoses_msm"]),
        rhm.WaveSpec(index=2, n_design=120, seed=2, target_series=["shm_new_diagnoses_msm"]),
    ]
    payload = rhm.run_waves(
        base_config={},
        calib_space=SPACE,
        register=register,
        waves=waves,
        stopping=rhm.StoppingRule(max_waves=2, min_volume_shrinkage=0.0, min_nroy_members=1),
        out_dir=tmp_path / "run",
        evaluate=fake_evaluate,
        scored_years=years,
    )

    history = payload["history"]
    assert len(history) == 2
    assert history[0]["n_nroy"] > 0
    # Wave 2 samples inside wave 1's retained range, so the retained fraction rises
    # while the retained VOLUME falls -- both must be reported, and they are.
    assert history[1]["frac_bounding_box"] <= history[0]["frac_bounding_box"]

    run_dir = tmp_path / "run"
    for wave in (1, 2):
        wave_dir = run_dir / f"wave_{wave:02d}"
        for name in ("design.csv", "evaluated.csv", "nroy_summary.csv",
                     "per_target_implausibility.csv", "binding_targets.csv",
                     "variance_budget.csv", "parameter_ranges.csv",
                     "nroy_members.csv", "wave_summary.json"):
            assert (wave_dir / name).exists(), f"missing {name} for wave {wave}"

    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["artefacts"]
    assert all(len(entry["sha256"]) == 64 for entry in manifest["artefacts"])
    assert "posterior" in manifest["vocabulary_note"]           # the prohibition is recorded
    assert manifest["stopping_rule"]["max_waves"] == 2

    members = pd.read_csv(run_dir / "wave_01" / "nroy_members.csv")
    assert (members["i_max"] <= hm.IMPLAUSIBILITY_CUTOFF).all()
    assert "binding_target" in members.columns


@needs_workbook
def test_wave_loop_stops_immediately_when_nroy_is_empty(tmp_path):
    register = tr.load_register()
    years = [2024]

    def hopeless(design, base_config, calib_space, *, seed, workers=1, run_settings=None):
        frame = design.copy()
        frame.insert(0, "design_id", range(len(frame)))
        frame["new_diagnoses_msm_2024"] = 1e9
        frame["error"] = ""
        return frame

    payload = rhm.run_waves(
        base_config={}, calib_space=SPACE, register=register,
        waves=[rhm.WaveSpec(index=1, n_design=40, seed=1, target_series=["shm_new_diagnoses_msm"]),
               rhm.WaveSpec(index=2, n_design=40, seed=2, target_series=["shm_new_diagnoses_msm"])],
        stopping=rhm.StoppingRule(max_waves=5, min_volume_shrinkage=0.1, min_nroy_members=10),
        out_dir=tmp_path / "run", evaluate=hopeless, scored_years=years,
    )
    assert len(payload["history"]) == 1
    assert payload["history"][0]["n_nroy"] == 0
    assert "RESULT, not a failure" in payload["stop_reason"]


@needs_workbook
def test_wave_loop_can_start_from_a_parent_nroy_region(tmp_path):
    register = tr.load_register()
    year = 2024
    z = next(t.z for t in register.targets if t.series_name == "shm_new_diagnoses_msm" and t.year == year)
    initial = pd.DataFrame({"a": [4.0, 6.0], "b": [-0.2, 0.3]})

    def fake_evaluate(design, base_config, calib_space, *, seed, workers=1, run_settings=None):
        frame = design.copy()
        frame.insert(0, "design_id", range(len(frame)))
        frame[f"new_diagnoses_msm_{year}"] = z
        frame["error"] = ""
        return frame

    payload = rhm.run_waves(
        base_config={}, calib_space=SPACE, register=register,
        waves=[rhm.WaveSpec(index=2, n_design=80, seed=2, target_series=["shm_new_diagnoses_msm"])],
        stopping=rhm.StoppingRule(max_waves=1, min_volume_shrinkage=0.1, min_nroy_members=1),
        out_dir=tmp_path / "continued", evaluate=fake_evaluate, scored_years=[year],
        initial_retained=initial,
        continuation_context={"source_dir": "/parent", "parent_last_wave": 1, "parent_nroy_members": 2},
    )
    design = pd.read_csv(tmp_path / "continued" / "wave_02" / "design.csv")
    assert design["a"].between(4.0, 6.0).all()
    assert design["b"].between(-0.2, 0.3).all()
    assert payload["continuation"]["parent_last_wave"] == 1
    summary = (tmp_path / "continued" / "RUN_SUMMARY.md").read_text()
    assert "Continuation source" in summary


# --------------------------------------------------------------------------
# Guardrails
# --------------------------------------------------------------------------

def test_cli_refuses_to_run_without_the_prerequisite_acknowledgement(capsys):
    code = rhm.main([
        "--wave-plan", "x", "--base-config", "y", "--calib-space", "s", "--output-dir", "z"
    ])
    assert code == 2
    err = capsys.readouterr().err
    assert "REFUSING TO RUN" in err
    for token in ("start years agree", "seed years agree", "C-1", "V_ensemble", "V_disc"):
        assert token in err


def test_v_ensemble_loader_divides_per_run_variance_by_replicate_count(tmp_path):
    path = tmp_path / "v.csv"
    pd.DataFrame({
        "target_key": ["a@2020"],
        "recommended_v_ensemble_per_run": [90.0],
        "recommended_estimator": ["empirical"],
    }).to_csv(path, index=False)
    terms = rhm.load_v_ensemble_terms(path, replicates_per_design_point=3)
    assert terms["a@2020"].variance == pytest.approx(30.0)
    assert "k=3" in terms["a@2020"].derivation


def test_evaluate_design_uses_common_replicate_seeds_for_every_point():
    seen = []

    def worker(payload):
        seen.append(tuple(payload["replicate_seeds"]))
        return {"design_id": payload["design_id"], "error": ""}

    design = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    rhm.evaluate_design(
        design,
        {},
        SPACE,
        seed=1,
        worker=worker,
        run_settings={
            "deterministic_calibration": False,
            "replicates_per_design_point": 3,
            "simulation_seed": 8000,
        },
    )
    assert seen == [(8000, 8001, 8002)] * 3


def test_manifest_checksums_every_artefact(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.csv").write_text("y\n2\n")
    path = rhm.write_manifest(tmp_path, {"note": "test"})
    manifest = json.loads(path.read_text())
    assert {entry["path"] for entry in manifest["artefacts"]} == {"a.csv", "nested/b.csv"}
    assert manifest["note"] == "test"


def _continuation_fixture(tmp_path):
    source = tmp_path / "parent"
    wave = source / "wave_03"
    wave.mkdir(parents=True)
    members = pd.DataFrame({
        "a": [4.0, 6.0],
        "b": [-0.2, 0.3],
        "i_max": [1.0, 2.0],
        "error": ["", ""],
    })
    members.to_csv(wave / "nroy_members.csv", index=False)
    provenance = {
        name: {"sha256": f"hash-{name}"}
        for name in ("base_config", "calib_space", "target_register", "v_ensemble")
    }
    provenance["runtime"] = {"modules": {"engine_v3.py": "same"}}
    rhm.write_manifest(source, {
        "cutoff": 3.0,
        "stop_reason": "max_waves=3 reached",
        "history": [{"wave": 3, "n_errors": 0, "n_nroy": 2}],
        "input_provenance": provenance,
    })
    return source, provenance


def test_continuation_loads_verified_final_nroy(tmp_path):
    source, provenance = _continuation_fixture(tmp_path)
    retained, context = rhm.load_continuation_source(
        source,
        current_provenance=provenance,
        calib_space=SPACE,
        waves=[rhm.WaveSpec(index=4, n_design=20, seed=4, target_series=["x"])],
        cutoff=3.0,
    )
    assert len(retained) == 2
    assert context["parent_last_wave"] == 3
    assert context["parent_nroy_members"] == 2
    assert context["runtime_module_differences"] == []


def test_continuation_rejects_a_corrupted_parent_artefact(tmp_path):
    source, provenance = _continuation_fixture(tmp_path)
    with (source / "wave_03" / "nroy_members.csv").open("a") as handle:
        handle.write("corruption\n")
    with pytest.raises(ValueError, match="integrity verification"):
        rhm.load_continuation_source(
            source,
            current_provenance=provenance,
            calib_space=SPACE,
            waves=[rhm.WaveSpec(index=4, n_design=20, seed=4, target_series=["x"])],
            cutoff=3.0,
        )


def test_continuation_requires_the_next_wave_index(tmp_path):
    source, provenance = _continuation_fixture(tmp_path)
    with pytest.raises(ValueError, match="must begin with wave index 4"):
        rhm.load_continuation_source(
            source,
            current_provenance=provenance,
            calib_space=SPACE,
            waves=[rhm.WaveSpec(index=5, n_design=20, seed=5, target_series=["x"])],
            cutoff=3.0,
        )
