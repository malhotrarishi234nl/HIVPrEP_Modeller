"""
Focused audit tests for the missing non-MSM diagnosis issue.

These tests do not change production behavior. They isolate whether the core
mechanics can count non-MSM diagnoses and generate heterosexual infections under
synthetic states. The full calibrated runs can still fail for structural or
target-composition reasons; these tests only guard the low-level accounting.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_v3 import SimulationEngineV3


CONFIG_PATH = ROOT / "1980_test.json"


def _load_config() -> dict:
    with CONFIG_PATH.open() as f:
        return json.load(f)


def _engine(cfg: dict | None = None) -> SimulationEngineV3:
    cfg = copy.deepcopy(cfg or _load_config())
    cfg["run_control"]["deterministic_mode"] = {"value": True}
    cfg["run_control"]["start_year"] = {"value": 2024}
    cfg["run_control"]["end_year"] = {"value": 2024}
    cfg["run_control"]["step_size"] = {"value": 3}
    return SimulationEngineV3(cfg, seed=123)


def _clear_population(engine: SimulationEngineV3) -> None:
    engine.state.population[...] = 0


def _idx(engine: SimulationEngineV3) -> dict[str, int]:
    return {
        "age": 0,
        "activity": engine.groups.activity_to_idx["High"],
        "female": engine.groups.orient_to_idx["Female"],
        "het_male": engine.groups.orient_to_idx["Heterosexual_Male"],
        "msm": engine.groups.orient_to_idx["MSM"],
        "susceptible": engine.groups.hiv_to_idx["Susceptible"],
        "chronic": engine.groups.hiv_to_idx["Chronic"],
        "undiagnosed": 0,
        "diagnosed": 1,
        "no_art": 0,
        "no_prep": engine.groups.prep_to_idx.get("None", 0),
    }


def _set_cell(
    engine: SimulationEngineV3,
    *,
    orient: int,
    hiv: int,
    diagnosis: int,
    n: int,
    activity: int | None = None,
) -> None:
    i = _idx(engine)
    engine.state.population[
        i["age"],
        orient,
        i["activity"] if activity is None else activity,
        hiv,
        diagnosis,
        i["no_art"],
        i["no_prep"],
    ] = n


def _set_flat_diagnosis_rate(cfg: dict, annual_rate: float) -> dict:
    cfg = copy.deepcopy(cfg)
    diag = cfg["transmission_model"]["disease"]["diagnosis"]
    diag.pop("schedule", None)
    diag["annual_rate"] = {"value": annual_rate}
    diag.pop("msm_relative_diagnosis", None)
    return cfg


def _set_msm_relative_diagnosis(
    cfg: dict,
    *,
    base_annual_rate: float,
    msm_multiplier: float,
) -> dict:
    cfg = copy.deepcopy(cfg)
    diag = cfg["transmission_model"]["disease"]["diagnosis"]
    diag.pop("schedule", None)
    diag["annual_rate"] = {"value": base_annual_rate}
    diag["msm_relative_diagnosis"] = {
        "enabled": {"value": True},
        "schedule": {
            "interpolation": {"value": "stepwise"},
            "value": {"2024": msm_multiplier},
        },
    }
    return cfg


def _set_high_vaginal_transmission(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    adv = cfg["transmission_model"]["disease"]["transmission"]["advanced"]
    adv["stage_multiplier_acute"] = {"value": 1.0}
    adv["stage_multiplier_aids"] = {"value": 1.0}
    for route in ["vaginal_mtf", "vaginal_ftm"]:
        adv["beta_per_route"][route]["chronic"]["value"] = 1.0
    disease_adv = cfg["transmission_model"]["disease"].setdefault("advanced", {})
    disease_adv["art_transmission_reduction"] = {"value": 0.0}
    return cfg


def test_default_diagnosis_path_counts_non_msm_and_msm_separately():
    cfg = _set_flat_diagnosis_rate(_load_config(), annual_rate=1.0)
    engine = _engine(cfg)
    _clear_population(engine)
    i = _idx(engine)

    _set_cell(engine, orient=i["female"], hiv=i["chronic"], diagnosis=i["undiagnosed"], n=10)
    _set_cell(engine, orient=i["het_male"], hiv=i["chronic"], diagnosis=i["undiagnosed"], n=20)
    _set_cell(engine, orient=i["msm"], hiv=i["chronic"], diagnosis=i["undiagnosed"], n=30)

    result = engine._step_diagnosis(2024.0)

    assert result == {"total": 60, "msm": 30}
    assert result["total"] - result["msm"] == 30


def test_msm_relative_diagnosis_path_keeps_non_msm_base_rate():
    cfg = _set_msm_relative_diagnosis(
        _load_config(),
        base_annual_rate=1.0,
        msm_multiplier=0.0,
    )
    engine = _engine(cfg)
    _clear_population(engine)
    i = _idx(engine)

    _set_cell(engine, orient=i["female"], hiv=i["chronic"], diagnosis=i["undiagnosed"], n=10)
    _set_cell(engine, orient=i["het_male"], hiv=i["chronic"], diagnosis=i["undiagnosed"], n=20)
    _set_cell(engine, orient=i["msm"], hiv=i["chronic"], diagnosis=i["undiagnosed"], n=30)

    result = engine._step_diagnosis(2024.0)

    assert result == {"total": 30, "msm": 0}
    assert result["total"] - result["msm"] == 30


def test_heterosexual_contact_matrix_has_bidirectional_contact_supply():
    engine = _engine()
    _clear_population(engine)
    i = _idx(engine)

    _set_cell(engine, orient=i["female"], hiv=i["susceptible"], diagnosis=i["undiagnosed"], n=10_000)
    _set_cell(engine, orient=i["het_male"], hiv=i["chronic"], diagnosis=i["undiagnosed"], n=10_000)

    contacts = engine._step_contacts_and_mixing()

    assert contacts[i["female"], i["activity"], i["het_male"], i["activity"]] > 0
    assert contacts[i["het_male"], i["activity"], i["female"], i["activity"]] > 0


def test_transmission_engine_can_generate_non_msm_infections_in_extreme_heterosexual_setup():
    cfg = _set_high_vaginal_transmission(_load_config())
    engine = _engine(cfg)
    _clear_population(engine)
    i = _idx(engine)

    _set_cell(engine, orient=i["female"], hiv=i["susceptible"], diagnosis=i["undiagnosed"], n=10_000)
    _set_cell(engine, orient=i["het_male"], hiv=i["chronic"], diagnosis=i["undiagnosed"], n=10_000)

    contacts = engine._step_contacts_and_mixing()
    total, infections = engine.transmission_engine.calculate_transmission(
        engine.state.population,
        contacts,
        engine.rng,
        deterministic=True,
    )
    new_inf_msm = int(np.sum(infections[:, i["msm"], :, :]))
    new_inf_non_msm = int(total - new_inf_msm)

    assert total > 0
    assert new_inf_non_msm > 0
    assert new_inf_msm == 0


def test_deterministic_transmission_preserves_sparse_non_msm_aggregate():
    cfg = _load_config()
    adv = cfg["transmission_model"]["disease"]["transmission"]["advanced"]
    adv["stage_multiplier_acute"] = {"value": 1.0}
    adv["stage_multiplier_aids"] = {"value": 1.0}
    adv["beta_per_route"]["vaginal_mtf"]["chronic"]["value"] = 0.001
    engine = _engine(cfg)
    _clear_population(engine)
    i = _idx(engine)

    low_activity = engine.groups.activity_to_idx["Low"]
    high_activity = engine.groups.activity_to_idx["High"]

    for age_idx in range(engine.groups.n_age_groups):
        engine.state.population[
            age_idx,
            i["female"],
            low_activity,
            i["susceptible"],
            i["undiagnosed"],
            i["no_art"],
            i["no_prep"],
        ] = 1

    _set_cell(
        engine,
        orient=i["het_male"],
        hiv=i["chronic"],
        diagnosis=i["undiagnosed"],
        activity=high_activity,
        n=1,
    )
    _set_cell(
        engine,
        orient=i["het_male"],
        hiv=i["susceptible"],
        diagnosis=i["undiagnosed"],
        activity=high_activity,
        n=1,
    )

    contacts = np.zeros(
        (
            engine.groups.n_orientations,
            engine.groups.n_activities,
            engine.groups.n_orientations,
            engine.groups.n_activities,
        ),
        dtype=float,
    )
    contacts[i["female"], low_activity, i["het_male"], high_activity] = 3_000.0

    total, infections = engine.transmission_engine.calculate_transmission(
        engine.state.population,
        contacts,
        engine.rng,
        deterministic=True,
    )
    new_inf_msm = int(np.sum(infections[:, i["msm"], :, :]))
    new_inf_non_msm = int(total - new_inf_msm)

    assert new_inf_non_msm == 2
    assert new_inf_msm == 0
    assert np.all(infections[:, i["female"], :, :] <= 1)
