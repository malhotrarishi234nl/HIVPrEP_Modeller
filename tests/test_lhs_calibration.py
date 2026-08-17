import copy
import json
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]

import run_lhs_calibration as lhs_module
from calib_data_loader import default_unaids_path, load_unaids_targets
from calib_extractor import extract_annual_outputs
from calib_scorer import is_collapsed, score_draw
from calib_target_loader import (
    default_disease_workbook_path,
    default_target_mapping_path,
    load_disease_targets,
)
from run_lhs_calibration import (
    filter_calib_space_for_stage,
    generate_lhs_draws,
    get_nested,
    inject_params,
    load_json,
    refine_calib_space_around_base,
    rerank_top_draws,
    set_nested,
    validate_config_paths,
)


def test_unaids_loader_uses_count_indicators_not_rate():
    targets = load_unaids_targets(default_unaids_path(ROOT))

    assert set(targets) == {"PLHIV", "new_infections", "aids_deaths"}
    assert targets["PLHIV"][2023]["estimate"] == pytest.approx(25461.25547)
    assert targets["new_infections"][2023]["estimate"] == pytest.approx(311.88949)
    assert targets["aids_deaths"][2023]["estimate"] == pytest.approx(70.8932)
    assert 2024 not in targets["PLHIV"]


def test_annual_extractor_uses_q4_stock_and_annual_flows():
    df = pd.DataFrame(
        {
            "year": [2020.0, 2020.25, 2020.5, 2020.75],
            "prevalence": [0.10, 0.11, 0.12, 0.20],
            "sexually_active_population": [1000, 1000, 1000, 2000],
            "new_infections": [1, 2, 3, 4],
            "aids_deaths": [5, 6, 7, 8],
            "new_diagnoses": [1, 1, 2, 2],
            "new_diagnoses_msm": [0, 1, 1, 1],
            "new_aids_cases": [2, 2, 2, 2],
            "art_initiations": [3, 3, 3, 3],
            "on_art_stock": [50, 55, 60, 70],
            "adult_hiv_pos": [100, 110, 120, 430],
            "plhiv_female_15plus": [20, 25, 30, 100],
            "plhiv_male_15plus": [80, 85, 90, 330],
            "plhiv_child_0_14": [0, 5, 10, 20],
        }
    )

    outputs = extract_annual_outputs({"summary": df}, start_year=2020, end_year=2020)

    assert outputs["PLHIV"][2020] == pytest.approx(450.0)
    assert outputs["new_infections"][2020] == pytest.approx(10.0)
    assert outputs["aids_deaths"][2020] == pytest.approx(26.0)
    assert outputs["new_diagnoses"][2020] == pytest.approx(6.0)
    assert outputs["new_diagnoses_msm"][2020] == pytest.approx(3.0)
    assert outputs["new_aids_cases"][2020] == pytest.approx(8.0)
    assert outputs["art_initiations"][2020] == pytest.approx(12.0)
    assert outputs["on_art_stock"][2020] == pytest.approx(70.0)
    assert outputs["plhiv_female_15plus"][2020] == pytest.approx(100.0)
    assert outputs["plhiv_male_15plus"][2020] == pytest.approx(330.0)
    assert outputs["plhiv_child_0_14"][2020] == pytest.approx(20.0)


def test_score_draw_marks_collapsed_epidemic_as_negative_infinity():
    model = {
        "PLHIV": {1995: 99.0},
        "new_infections": {year: 0.0 for year in range(1990, 1995)},
        "aids_deaths": {1995: 1.0},
    }
    targets = {
        "PLHIV": {1995: {"estimate": 1000.0, "sigma": 100.0}},
        "new_infections": {1995: {"estimate": 10.0, "sigma": 1.0}},
        "aids_deaths": {1995: {"estimate": 5.0, "sigma": 1.0}},
    }

    assert is_collapsed(model)
    scored = score_draw(model, targets)
    assert scored["collapsed"] is True
    assert scored["total_ll"] == float("-inf")


def test_score_draw_accepts_normalized_target_records():
    model = {
        "PLHIV": {2020: 1000.0},
        "new_infections": {2020: 50.0},
        "aids_deaths": {2020: 5.0},
        "new_diagnoses": {2020: 40.0},
    }
    targets = [
        {
            "model_output": "new_diagnoses",
            "year": 2020,
            "estimate": 42.0,
            "sigma": 4.2,
            "weight": 1.5,
        }
    ]

    scored = score_draw(model, targets)

    assert scored["collapsed"] is False
    assert scored["n_years_scored"] == 1
    assert "new_diagnoses" in scored["ll_by_indicator"]


def test_score_draw_can_balance_target_families():
    model = {"a": {2020: 0.0, 2021: 0.0}, "b": {2020: 0.0}}
    targets = [
        {
            "model_output": "a",
            "target_family": "dense_family",
            "year": 2020,
            "estimate": 10.0,
            "sigma": 1.0,
            "weight": 1.0,
        },
        {
            "model_output": "a",
            "target_family": "dense_family",
            "year": 2021,
            "estimate": 10.0,
            "sigma": 1.0,
            "weight": 1.0,
        },
        {
            "model_output": "b",
            "target_family": "sparse_family",
            "year": 2020,
            "estimate": 0.0,
            "sigma": 1.0,
            "weight": 1.0,
        },
    ]

    raw = score_draw(model, targets)
    balanced = score_draw(model, targets, balance_families=True)

    assert balanced["total_ll"] > raw["total_ll"]
    assert balanced["ll_by_family"]["dense_family"] < 0.0
    assert balanced["n_by_family"]["dense_family"] == 2


def test_disease_workbook_loader_preserves_source_columns_via_mapping():
    targets = load_disease_targets(
        default_disease_workbook_path(ROOT),
        default_target_mapping_path(ROOT),
        stage="backbone",
        start_year=2023,
        end_year=2023,
    )

    by_output = {target["model_output"]: target for target in targets}
    assert by_output["PLHIV"]["estimate"] == pytest.approx(25461.25547)
    assert by_output["plhiv_female_15plus"]["estimate"] == pytest.approx(4726.54864)
    assert by_output["plhiv_male_15plus"]["estimate"] == pytest.approx(20594.66392)
    assert by_output["new_infections"]["estimate"] == pytest.approx(309.85524)
    assert by_output["aids_deaths"]["estimate"] == pytest.approx(68.70016)
    assert {target["source_sheet"] for target in targets} == {
        "UNAIDS-PLHIV)",
        "UNAIDS-HIV Infections",
        "UNAIDS-AIDSDeath",
    }


def test_strict_set_nested_requires_existing_paths():
    cfg = {"a": {"b": [{"c": 1}]}}

    set_nested(cfg, "a.b[0].c", 2)

    assert get_nested(cfg, "a.b[0].c") == 2
    with pytest.raises(KeyError):
        set_nested(cfg, "a.b[1].c", 3)
    with pytest.raises(KeyError):
        set_nested(cfg, "a.x.c", 3)


def test_inject_params_deep_copies_base_config_and_disables_outputs_only_in_copy():
    base_config = load_json(ROOT / "1980_test.json")
    calib_space = load_json(ROOT / "configs" / "calib_space.json")
    validate_config_paths(base_config, calib_space)
    original = copy.deepcopy(base_config)
    vector = {
        "orient_female": 0.8,
        "orient_msm": 3.2,
        "epsilon_hetero": 0.3,
        "epsilon_msm": 0.4,
        "activity_med": 0.35,
        "activity_high": 0.10,
    }

    injected = inject_params(base_config, vector, calib_space)

    assert base_config == original
    assert get_nested(injected, "population.activity_distribution.value.Low") == pytest.approx(0.55)
    assert get_nested(injected, "transmission_model.groups.advanced.orientation_contact_multipliers.value.Female") == pytest.approx(0.8)
    assert get_nested(injected, "transmission_model.groups.advanced.orientation_contact_multipliers.value.Heterosexual_Male") == pytest.approx(1.0)
    assert get_nested(injected, "transmission_model.groups.advanced.orientation_contact_multipliers.value.MSM") == pytest.approx(3.2)
    assert get_nested(injected, "transmission_model.disease.transmission.advanced.beta_per_route.vaginal_mtf.chronic.value") == get_nested(
        base_config,
        "transmission_model.disease.transmission.advanced.beta_per_route.vaginal_mtf.chronic.value",
    )
    assert get_nested(injected, "output.enabled.value") is False
    assert get_nested(injected, "output.terminal_summary.value") is False
    assert get_nested(base_config, "output.enabled.value") == get_nested(original, "output.enabled.value")


def test_inject_params_can_force_deterministic_mode_for_scoring_copy():
    base_config = load_json(ROOT / "1980_test.json")
    calib_space = load_json(ROOT / "configs" / "calib_space.json")

    injected = inject_params(
        base_config,
        {"activity_med": 0.35, "activity_high": 0.10},
        calib_space,
        deterministic_mode=True,
    )

    assert get_nested(injected, "run_control.deterministic_mode.value") is True
    assert get_nested(base_config, "run_control.deterministic_mode.value") is False


def test_best_fit_configs_preserve_deterministic_calibration_mode(tmp_path):
    base_config = load_json(ROOT / "1980_test.json")
    calib_space = load_json(ROOT / "configs" / "calib_space.json")
    ranked = pd.DataFrame(
        [
            {
                "draw_id": 1,
                "total_ll": -10.0,
                "activity_med": 0.35,
                "activity_high": 0.10,
            }
        ]
    )

    paths = lhs_module.write_best_fit_configs(
        ranked,
        base_config,
        calib_space,
        tmp_path,
        top_n=1,
        metadata_stub={"deterministic_calibration": True},
    )

    best = load_json(Path(paths[0]))
    assert get_nested(best, "run_control.deterministic_mode.value") is True
    assert (
        best["calibration_metadata"]["lhs_calibration"]["deterministic_calibration"]
        is True
    )
    assert get_nested(base_config, "run_control.deterministic_mode.value") is False


def test_refine_calib_space_around_base_narrows_active_bounds():
    base_config = load_json(ROOT / "1980_test.json")
    calib_space = load_json(ROOT / "configs" / "calib_space.json")
    backbone_space = filter_calib_space_for_stage(calib_space, "backbone")
    set_nested(base_config, "population.activity_distribution.value.Medium", 0.325)

    refined = refine_calib_space_around_base(base_config, backbone_space, fraction=0.25)
    by_name = {param["name"]: param for param in refined["parameters"]}
    original = {param["name"]: param for param in backbone_space["parameters"]}

    assert by_name["activity_med"]["max"] - by_name["activity_med"]["min"] == pytest.approx(
        (original["activity_med"]["max"] - original["activity_med"]["min"]) * 0.25
    )
    assert by_name["activity_med"]["min"] <= get_nested(
        base_config,
        original["activity_med"]["config_path"],
    ) <= by_name["activity_med"]["max"]
    assert "refined_from" in by_name["activity_med"]


def test_rerank_top_draws_prioritizes_replicated_candidates(monkeypatch):
    ranked = pd.DataFrame(
        {
            "draw_id": [1, 2, 3],
            "x": [0.1, 0.2, 0.3],
            "total_ll": [100.0, 90.0, 80.0],
            "collapsed": [False, False, False],
            "n_years_scored": [1, 1, 1],
        }
    )

    def fake_run_payloads(payloads, workers):
        rows = []
        for payload in payloads:
            rows.append(
                {
                    "draw_id": payload["draw_id"],
                    "x": payload["params"]["x"],
                    "total_ll": -1000.0 if payload["draw_id"] == 1 else -900.0,
                    "collapsed": False,
                    "n_years_scored": 1,
                    "missing_outputs": "",
                    "error": "",
                }
            )
        return rows

    monkeypatch.setattr(lhs_module, "_run_payloads", fake_run_payloads)

    reranked = rerank_top_draws(
        ranked,
        base_config={},
        calib_space={"parameters": [{"name": "x"}]},
        targets=[],
        seed=1,
        top_n=2,
        replicates=2,
        workers=1,
        balance_target_families=False,
    )

    assert list(reranked["draw_id"].head(2)) == [2, 1]
    assert reranked.iloc[0]["total_ll"] == pytest.approx(-900.0)
    assert reranked.iloc[2]["draw_id"] == 3


def test_generate_lhs_draws_applies_activity_constraints_and_excludes_fixed_inputs():
    calib_space = load_json(ROOT / "configs" / "calib_space.json")
    backbone_space = filter_calib_space_for_stage(calib_space, "backbone")

    draws = generate_lhs_draws(backbone_space["parameters"], n=25, seed=123)

    assert len(draws) == 25
    active_names = {param["name"] for param in backbone_space["parameters"]}
    assert active_names == {
        "orient_female",
        "orient_msm",
        "epsilon_hetero",
        "epsilon_msm",
        "activity_med",
        "activity_high",
        "seed_prevalence",
        "seed_msm_fraction",
        "contact_low",
        "contact_med",
        "contact_high",
    }
    assert "orient_het_male" not in active_names
    assert not any(name.startswith("beta_") for name in active_names)
    for draw in draws:
        assert draw["orient_female"] <= 1.0
        assert draw["orient_msm"] >= 1.0
        assert draw["orient_msm"] >= draw["orient_female"]
        assert 0.14 <= draw["activity_low"] <= 0.81
        total = draw["activity_low"] + draw["activity_med"] + draw["activity_high"]
        assert total == pytest.approx(1.0)
        assert draw["contact_low"] < draw["contact_med"] < draw["contact_high"]


def test_generate_lhs_draws_neighboring_seeds_do_not_reuse_retry_batches():
    calib_space = load_json(ROOT / "configs" / "calib_space.json")
    joint_space = filter_calib_space_for_stage(calib_space, "joint")

    draws_a = generate_lhs_draws(joint_space["parameters"], n=200, seed=45)
    draws_b = generate_lhs_draws(joint_space["parameters"], n=200, seed=46)

    def signature(draw):
        return tuple((name, round(float(value), 14)) for name, value in sorted(draw.items()))

    assert {signature(draw) for draw in draws_a}.isdisjoint({signature(draw) for draw in draws_b})


def test_inject_params_rejects_invalid_orientation_ordering():
    base_config = load_json(ROOT / "1980_test.json")
    calib_space = load_json(ROOT / "configs" / "calib_space.json")
    vector = {
        "orient_female": 1.2,
        "orient_msm": 0.9,
        "epsilon_hetero": 0.3,
        "epsilon_msm": 0.4,
        "activity_med": 0.35,
        "activity_high": 0.10,
        "seed_prevalence": 0.001,
        "seed_msm_fraction": 0.8,
        "contact_low": 3.0,
        "contact_med": 10.0,
        "contact_high": 50.0,
    }

    with pytest.raises(ValueError, match="orient_female must be <="):
        inject_params(base_config, vector, calib_space)


def test_stage_filter_selects_diagnosis_parameters_only():
    calib_space = load_json(ROOT / "configs" / "calib_space.json")
    diagnosis_space = filter_calib_space_for_stage(calib_space, "diagnosis")

    assert {param["name"] for param in diagnosis_space["parameters"]} == {
        "diagnosis_1985",
        "diagnosis_1990",
        "diagnosis_1997",
        "diagnosis_2019",
    }


def test_generate_lhs_draws_applies_diagnosis_and_art_schedule_constraints():
    calib_space = load_json(ROOT / "configs" / "calib_space.json")
    joint_space = filter_calib_space_for_stage(calib_space, "joint")

    draws = generate_lhs_draws(joint_space["parameters"], n=50, seed=321)

    for draw in draws:
        assert draw["diagnosis_1985"] <= draw["diagnosis_1990"]
        assert draw["diagnosis_1990"] <= draw["diagnosis_1997"]
        assert draw["diagnosis_1997"] <= draw["diagnosis_2019"]
        assert draw["art_init_1996"] <= draw["art_init_2000"]
        assert draw["art_init_2000"] <= draw["art_init_2015"]
        assert draw["art_init_2015"] <= draw["art_init_2020"]


# ---------------------------------------------------------------------------
# V13.9.3 p_msm synchronization tests
# ---------------------------------------------------------------------------


class TestPMsmSync:
    """V13.9.3 MSM denominator diagnostic: p_msm calibration space and sync."""

    def _calib_space_with_p_msm(self) -> dict:
        return {
            "parameters": [
                {
                    "name": "p_msm",
                    "config_path": "population.p_msm.value",
                    "min": 0.053,
                    "max": 0.090,
                    "distribution": "uniform",
                    "unit": "proportion",
                    "stages": ["joint"],
                },
                {
                    "name": "activity_med",
                    "config_path": "population.activity_distribution.value.Medium",
                    "min": 0.20,
                    "max": 0.45,
                    "distribution": "uniform",
                    "unit": "proportion",
                    "stages": ["joint"],
                },
                {
                    "name": "activity_high",
                    "config_path": "population.activity_distribution.value.High",
                    "min": 0.03,
                    "max": 0.15,
                    "distribution": "uniform",
                    "unit": "proportion",
                    "stages": ["joint"],
                },
                {
                    "name": "seed_chronic_share",
                    "config_path": "population.epidemic_seed.stage_distribution.value.Chronic",
                    "min": 0.0,
                    "max": 0.40,
                    "distribution": "uniform",
                    "unit": "proportion",
                    "stages": ["joint"],
                },
                {
                    "name": "seed_aids_share",
                    "config_path": "population.epidemic_seed.stage_distribution.value.AIDS",
                    "min": 0.0,
                    "max": 0.05,
                    "distribution": "uniform",
                    "unit": "proportion",
                    "stages": ["joint"],
                },
            ],
            "fixed_parameters": [],
            "excluded_parameters": [],
        }

    def test_p_msm_config_path_exists_in_base_config(self):
        base_config = load_json(ROOT / "1980_test.json")
        calib_space = self._calib_space_with_p_msm()
        validate_config_paths(base_config, calib_space)

    def test_inject_params_syncs_population_p_msm(self):
        base_config = load_json(ROOT / "1980_test.json")
        calib_space = self._calib_space_with_p_msm()
        vector = {
            "p_msm": 0.075,
            "activity_med": 0.25,
            "activity_high": 0.08,
            "seed_chronic_share": 0.15,
            "seed_aids_share": 0.02,
        }
        cfg = inject_params(base_config, vector, calib_space)
        assert get_nested(cfg, "population.p_msm.value") == pytest.approx(0.075)

    def test_inject_params_syncs_debut_p_msm_to_same_value(self):
        base_config = load_json(ROOT / "1980_test.json")
        calib_space = self._calib_space_with_p_msm()
        vector = {
            "p_msm": 0.075,
            "activity_med": 0.25,
            "activity_high": 0.08,
            "seed_chronic_share": 0.15,
            "seed_aids_share": 0.02,
        }
        cfg = inject_params(base_config, vector, calib_space)
        assert get_nested(cfg, "demography.sexual_debut.p_msm.value") == pytest.approx(0.075)

    def test_inject_params_without_p_msm_leaves_debut_p_msm_unchanged(self):
        base_config = load_json(ROOT / "1980_test.json")
        original_debut = get_nested(base_config, "demography.sexual_debut.p_msm.value")
        calib_space = {
            "parameters": [
                {
                    "name": "activity_med",
                    "config_path": "population.activity_distribution.value.Medium",
                    "min": 0.20,
                    "max": 0.45,
                },
                {
                    "name": "activity_high",
                    "config_path": "population.activity_distribution.value.High",
                    "min": 0.03,
                    "max": 0.15,
                },
            ],
            "fixed_parameters": [],
            "excluded_parameters": [],
        }
        vector = {"activity_med": 0.25, "activity_high": 0.08}
        cfg = inject_params(base_config, vector, calib_space)
        assert get_nested(cfg, "demography.sexual_debut.p_msm.value") == pytest.approx(original_debut)

    def test_derived_activity_low_correct_when_p_msm_present(self):
        base_config = load_json(ROOT / "1980_test.json")
        calib_space = self._calib_space_with_p_msm()
        vector = {
            "p_msm": 0.070,
            "activity_med": 0.30,
            "activity_high": 0.10,
            "seed_chronic_share": 0.20,
            "seed_aids_share": 0.03,
        }
        cfg = inject_params(base_config, vector, calib_space)
        assert get_nested(cfg, "population.activity_distribution.value.Low") == pytest.approx(
            round(1.0 - 0.30 - 0.10, 10)
        )

    def test_derived_seed_acute_share_correct_when_p_msm_present(self):
        base_config = load_json(ROOT / "1980_test.json")
        calib_space = self._calib_space_with_p_msm()
        vector = {
            "p_msm": 0.060,
            "activity_med": 0.25,
            "activity_high": 0.08,
            "seed_chronic_share": 0.20,
            "seed_aids_share": 0.02,
        }
        cfg = inject_params(base_config, vector, calib_space)
        stage_dist_acute = get_nested(cfg, "population.epidemic_seed.stage_distribution.value.Acute")
        assert stage_dist_acute == pytest.approx(round(1.0 - 0.20 - 0.02, 10))

    def test_base_config_not_mutated_by_p_msm_injection(self):
        base_config = load_json(ROOT / "1980_test.json")
        import copy
        original = copy.deepcopy(base_config)
        calib_space = self._calib_space_with_p_msm()
        vector = {
            "p_msm": 0.080,
            "activity_med": 0.25,
            "activity_high": 0.08,
            "seed_chronic_share": 0.15,
            "seed_aids_share": 0.02,
        }
        inject_params(base_config, vector, calib_space)
        assert get_nested(base_config, "population.p_msm.value") == pytest.approx(
            get_nested(original, "population.p_msm.value")
        )
        assert get_nested(base_config, "demography.sexual_debut.p_msm.value") == pytest.approx(
            get_nested(original, "demography.sexual_debut.p_msm.value")
        )

    def test_v13_9_3_calib_space_json_is_valid(self):
        calib_space = load_json(
            ROOT / "configs" / "calib_space_v13_9_3_msm_denominator_bridge.json"
        )
        assert calib_space["version"] == "1.3"
        param_names = {p["name"] for p in calib_space["parameters"]}
        assert "p_msm" in param_names
        p_msm = next(p for p in calib_space["parameters"] if p["name"] == "p_msm")
        assert p_msm["config_path"] == "population.p_msm.value"
        assert p_msm["min"] == pytest.approx(0.053)
        assert p_msm["max"] == pytest.approx(0.090)
        assert "joint" in p_msm["stages"]

    def test_v13_9_3_calib_space_passes_validate_config_paths(self):
        base_config = load_json(ROOT / "1980_test.json")
        calib_space = load_json(
            ROOT / "configs" / "calib_space_v13_9_3_msm_denominator_bridge.json"
        )
        validate_config_paths(base_config, calib_space)
