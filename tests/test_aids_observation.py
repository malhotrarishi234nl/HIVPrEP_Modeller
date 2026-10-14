"""Synthetic accounting tests; no epidemic calibration or production run."""
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest

from aids_observation import (
    AIDS_OBSERVATION_FIELDS, progression_observation_counts,
    diagnosis_observation_counts,
)
from calib_extractor import extract_annual_outputs
from run_v3 import SimulationEngineV3


def engine(grouped=False):
    e = object.__new__(SimulationEngineV3)
    e.state = SimpleNamespace(population=np.zeros((1, 3, 1, 4, 2, 2, 1), dtype=int))
    e.groups = SimpleNamespace(
        hiv_to_idx={'Susceptible': 0, 'Acute': 1, 'Chronic': 2, 'AIDS': 3},
        orient_to_idx={'Female': 0, 'MSM': 1, 'Heterosexual_Male': 2},
    )
    e.config = {'disease': {'progression': {'acute_to_chronic': 1, 'chronic_to_aids': 1},
                            'diagnosis': {'annual_rate': 1}}}
    e.time = SimpleNamespace(annual_to_step_rate=lambda x: x)
    e.diagnosis_schedule = None
    e.msm_relative_diagnosis_schedule = SimpleNamespace(get_value=lambda y: 1) if grouped else None
    e.draw_calls = []
    def draw(name, pool, rate):
        e.draw_calls.append(name)
        return pool.copy()  # 100% test rate, with no RNG
    e._draw_events_for = draw
    return e


@pytest.mark.parametrize('grouped', [False, True])
def test_same_step_progression_then_diagnosis_is_separable(grouped):
    e = engine(grouped)
    p = e.state.population
    p[0, 1, 0, 2, 0, 0, 0] = 5  # MSM, not yet diagnosed
    p[0, 0, 0, 2, 1, 1, 0] = 7  # non-MSM, already diagnosed/on ART
    p[0, 2, 0, 3, 0, 0, 0] = 3  # pre-existing AIDS, not yet diagnosed
    progression = e._step_disease_progression()
    diagnosis = e._step_diagnosis(2000)
    a = progression['aids_observation']
    b = diagnosis['aids_observation']
    assert progression['new_aids'] == 12  # legacy biological counter unchanged
    assert a['aids_progression_while_undiagnosed'] == 5
    assert a['aids_progression_while_diagnosed'] == 7
    assert b['hiv_diagnoses_in_aids_stage'] == 8
    assert b['hiv_diagnoses_in_aids_stage_msm'] == 5
    assert b['hiv_diagnoses_in_aids_stage_non_msm'] == 3
    # A recognition proxy would use 7+8, NOT 12+8, avoiding double-counting 5.
    assert a['aids_progression_while_diagnosed'] + b['hiv_diagnoses_in_aids_stage'] == 15
    assert p.sum() == 15
    assert len(e.draw_calls) == (5 if grouped else 4)
    assert e._step_diagnosis(2000)['aids_observation']['hiv_diagnoses_in_aids_stage'] == 0


def test_diagnostics_do_not_mutate_draws_and_partition_groups():
    off = np.arange(12).reshape(1, 3, 2, 2, 1)
    on = off * 2
    originals = off.copy(), on.copy()
    result = progression_observation_counts(off, on, 1)
    for stem in ('aids_progression_while_undiagnosed', 'aids_progression_while_diagnosed'):
        assert result[stem] == result[stem+'_msm'] + result[stem+'_non_msm']
    assert sum(result[s] for s in ('aids_progression_while_undiagnosed', 'aids_progression_while_diagnosed')) == off.sum()+on.sum()
    np.testing.assert_array_equal(off, originals[0])
    np.testing.assert_array_equal(on, originals[1])
    assert progression_observation_counts(off, on, None)['aids_progression_while_diagnosed_msm'] == 0


def test_non_aids_hiv_diagnoses_are_not_aids_presentations():
    draw = np.ones((1, 3, 1, 3, 2, 1), dtype=int)
    draw[:, :, :, 2] = 0
    assert diagnosis_observation_counts(draw, 3, 1)['hiv_diagnoses_in_aids_stage'] == 0


def test_annual_export_preserves_flows_and_missing_values():
    df = pd.DataFrame({'year': [2000, 2000.25], 'prevalence': [0, 0],
                       'sexually_active_population': [10, 10], 'new_infections': [0, 0],
                       'aids_deaths': [0, 0], **{f: [2, 3] for f in AIDS_OBSERVATION_FIELDS}})
    annual = extract_annual_outputs(df)
    assert all(annual[f][2000] == 5 for f in AIDS_OBSERVATION_FIELDS)
    df.loc[0, AIDS_OBSERVATION_FIELDS[0]] = np.nan
    assert np.isnan(extract_annual_outputs(df)[AIDS_OBSERVATION_FIELDS[0]][2000])
    legacy = extract_annual_outputs(df.drop(columns=list(AIDS_OBSERVATION_FIELDS)))
    assert not set(AIDS_OBSERVATION_FIELDS) & set(legacy)


@pytest.mark.parametrize('variant', [
    'current', 'mortality_before_diagnosis_art', 'art_lag_after_diagnosis',
    'mortality_before_progression',
])
def test_two_step_smoke_diagnostics_do_not_change_legacy_outputs(monkeypatch, variant):
    """Two timesteps only, comparing enabled accounting with no-op accounting."""
    import run_v3
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'configs/1980_test.json'
    normal = SimulationEngineV3(str(path), seed=123)
    normal.output_writer.enabled = False
    normal._diagnostic_event_order_variant = variant
    normal.run(end_step=2)
    expected_population = normal.state.population.copy()
    expected = pd.DataFrame(normal.outputs)
    del normal
    monkeypatch.setattr(run_v3, 'progression_observation_counts', lambda *args, **kwargs: {})
    monkeypatch.setattr(run_v3, 'diagnosis_observation_counts', lambda *args: {})
    control = SimulationEngineV3(str(path), seed=123)
    control.output_writer.enabled = False
    control._diagnostic_event_order_variant = variant
    control.run(end_step=2)
    np.testing.assert_array_equal(control.state.population, expected_population)
    legacy = [c for c in expected if c not in AIDS_OBSERVATION_FIELDS]
    pd.testing.assert_frame_equal(expected[legacy], pd.DataFrame(control.outputs)[legacy])
    assert expected[list(AIDS_OBSERVATION_FIELDS)].notna().all().all()
    np.testing.assert_array_equal(
        expected['new_aids_cases'],
        expected['aids_progression_while_undiagnosed'] + expected['aids_progression_while_diagnosed'],
    )


def test_art_split_partitions_diagnosed_progression_and_reconstructs_person_time():
    off = np.arange(12).reshape(1, 3, 2, 2, 1)          # age, orient, activity, diagnosis, prep
    on = off * 2
    pool_off_after = np.full_like(off, 10)
    pool_on_after = np.full_like(on, 20)
    originals = off.copy(), on.copy(), pool_off_after.copy(), pool_on_after.copy()
    r = progression_observation_counts(off, on, 1, pool_off_art_after=pool_off_after,
                                       pool_on_art_after=pool_on_after, years_per_step=0.25)
    assert r['aids_progression_while_diagnosed_off_art'] + r['aids_progression_while_diagnosed_on_art'] \
        == r['aids_progression_while_diagnosed']
    for stem in ('aids_progression_while_diagnosed_off_art', 'chronic_diagnosed_on_art_person_years'):
        assert r[stem] == pytest.approx(r[stem + '_msm'] + r[stem + '_non_msm'])
    expected_off_py = 0.25 * (pool_off_after[:, :, :, 1, :] + off[:, :, :, 1, :]).sum()
    assert r['chronic_diagnosed_off_art_person_years'] == pytest.approx(expected_off_py)
    for array, original in zip((off, on, pool_off_after, pool_on_after), originals):
        np.testing.assert_array_equal(array, original)


def test_art_split_person_time_is_nan_not_zero_without_pools():
    off = np.ones((1, 3, 1, 2, 1), dtype=int)
    r = progression_observation_counts(off, off, 1)
    assert np.isnan(r['chronic_diagnosed_off_art_person_years'])
    assert r['aids_progression_while_diagnosed_on_art'] == 3


def test_engine_person_time_equals_pre_draw_diagnosed_chronic_stock():
    e = engine()
    e.time = SimpleNamespace(annual_to_step_rate=lambda x: 0.0, years_per_step=0.25)
    e._draw_events_for = lambda name, pool, rate: np.zeros_like(pool)
    p = e.state.population
    p[0, 1, 0, 2, 1, 0, 0] = 8   # MSM diagnosed off ART
    p[0, 0, 0, 2, 1, 1, 0] = 4   # non-MSM diagnosed on ART
    p[0, 1, 0, 2, 0, 0, 0] = 99  # undiagnosed: not at risk of diagnosed-stage progression
    a = e._step_disease_progression()['aids_observation']
    assert a['chronic_diagnosed_off_art_person_years_msm'] == pytest.approx(2.0)
    assert a['chronic_diagnosed_on_art_person_years_non_msm'] == pytest.approx(1.0)
    assert a['chronic_diagnosed_off_art_person_years_non_msm'] == 0
