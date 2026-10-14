"""Guards for isolated historical-transmission diagnostics and 1990 preparation."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import historical_transmission_probe as H
import run_historical_transmission_probe as R


def schedule(levels=(2., 4.)):
    return H.ContactSchedule((1990., 2000.), levels, 2., 1., 5.)


@pytest.mark.parametrize('year,expected', [(1975, 1), (1990, 1), (1995, 1.5), (2000, 2), (2025, 2)])
def test_interpolation_and_explicit_endpoint_holds(year, expected):
    assert schedule().factor(year) == expected


@pytest.mark.parametrize('years,levels,reference', [
    ((1990, 1990), (2, 3), 2), ((2000, 1990), (2, 3), 2),
    ((1990, 2000), (2,), 2), ((1990, 2000), (2, float('nan')), 2),
    ((1990, 2000), (2, 6), 2), ((1990, 2000), (2, 3), 0),
])
def test_invalid_schedules_fail_without_clipping(years, levels, reference):
    with pytest.raises(ValueError):
        H.ContactSchedule(years, levels, reference, 1, 5)


def test_profiles_stay_in_existing_bounds_and_keep_neutral_reference():
    for ref in (1., 2.5, 5.):
        profiles = H.build_profiles(ref, 1, 5)
        assert len(profiles) == 5
        for year in np.linspace(1975, 2025, 101):
            assert profiles['constant'].factor(year) == 1
            for p in profiles.values():
                assert 1 <= p.factor(year) * ref <= 5
        assert profiles['early_up'].factor(2000) == 1
        assert profiles['middle_down'].factor(1990) == 1
        assert profiles['recent_down'].factor(2000) == 1


def test_only_msm_symmetric_contact_block_changes_and_input_is_preserved():
    a = np.arange(81, dtype=float).reshape(3, 3, 3, 3)
    a[1, :, 1, :] = np.eye(3) + np.ones((3, 3))
    before = a.copy()
    b = H.msm_contacts(a, 1, 1.5)
    np.testing.assert_array_equal(a, before)
    np.testing.assert_array_equal(b[1, :, 1, :], 1.5*a[1, :, 1, :])
    mask = np.ones(a.shape, dtype=bool); mask[1, :, 1, :] = False
    np.testing.assert_array_equal(b[mask], a[mask])
    np.testing.assert_array_equal(b[1, :, 1, :], b[1, :, 1, :].T)
    assert H.msm_contacts(a, 1, 1) is a


def test_adapter_calls_existing_contact_builder_and_does_not_modify_config(monkeypatch):
    a = np.ones((3, 2, 3, 2))
    monkeypatch.setattr(H.SimulationEngineV3, '_step_contacts_and_mixing', lambda self: a)
    e = object.__new__(H.HistoricalProbeEngine)
    e.groups = SimpleNamespace(orient_to_idx={'MSM': 1})
    e.contact_schedule = schedule()
    e._current_year = 1995
    e.contact_trace = []
    e.config = {'untouched': [1, 2, 3]}
    original = copy.deepcopy(e.config)
    b = e._step_contacts_and_mixing()
    assert b[1, 0, 1, 0] == 1.5
    assert b[0, 0, 2, 0] == 1
    assert e.config == original
    assert e.contact_trace == [(1995., 1.5)]


def test_start_of_year_capture_precedes_any_transitions(monkeypatch):
    e = object.__new__(H.HistoricalProbeEngine)
    e.time = SimpleNamespace(start_year=1975, years_per_step=.25)
    e.groups = SimpleNamespace(orient_to_idx={'MSM': 1})
    e.state = SimpleNamespace(population=np.zeros((1, 3, 1, 4, 2, 2, 1), dtype=int))
    e.state.population[0, 1, 0, 2, 0, 0, 0] = 7
    e.demography = SimpleNamespace(childhood=SimpleNamespace(population=np.array([10])))
    e.probe_states = {}
    monkeypatch.setattr(H.SimulationEngineV3, 'step_simulation', lambda self, step: self.state.population.fill(99))
    e.step_simulation(60)
    assert e.probe_states['1990']['population'].sum() == 7
    assert e.probe_states['1990']['summary']['msm_hiv_dx_art'][2][0][0] == 7
    assert e.probe_states['1990']['childhood_population'].sum() == 10


def test_readiness_rejects_annual_diagnoses_as_initial_stock():
    c = json.loads(R.CONTRACT.read_text())
    assert not H.check_initialisation_contract(c)['ready_for_initialisation_design']
    required = [k for k, v in c.items() if v is None]
    for k in required:
        c[k] = {'source': 'explicit assumption for synthetic test', 'definition': 'test rule',
                'quantity_type': 'stock_or_initialisation_rule'}
    assert H.check_initialisation_contract(c)['ready_for_initialisation_design']
    assert not H.check_initialisation_contract(c)['simulation_ready']
    c['hiv_stock_by_stage_diagnosis_art']['quantity_type'] = 'annual_diagnoses'
    assert not H.check_initialisation_contract(c)['ready_for_initialisation_design']


def test_prepare_real_inputs_is_read_only_and_never_constructs_engine(monkeypatch):
    monkeypatch.setattr(H.SimulationEngineV3, '__init__', lambda *a, **k: pytest.fail('engine constructed'))
    report, _ = R.prepare()
    assert len(report['cells']) == 10 and len(report['targets']) == 15
    assert report['simulations_started'] == 0
    assert report['planned_diagnostic_simulations'] == 50
    assert not report['initialisation_1990']['simulation_ready']


def test_cli_default_does_not_run_or_create_output(monkeypatch, tmp_path):
    monkeypatch.setattr(R, 'prepare', lambda: ({'status': 'test'}, {}))
    monkeypatch.setattr(R, 'simulate', lambda *a: pytest.fail('simulation'))
    monkeypatch.setattr(R, 'smoke', lambda *a: pytest.fail('smoke'))
    out = tmp_path/'absent'
    assert R.main(['--output-dir', str(out)]) == 0
    assert not out.exists()


def test_existing_output_is_not_overwritten(monkeypatch, tmp_path):
    keep = tmp_path/'keep'; keep.write_text('preserve')
    monkeypatch.setattr(R, 'prepare', lambda: ({}, {}))
    with pytest.raises(R.previous.ValidationError):
        R.main(['--prepare', '--output-dir', str(tmp_path)])
    assert keep.read_text() == 'preserve'


def test_scoring_uses_local_predictive_variance_and_paired_seeds():
    targets = [{'key': 'x', 'column': 'x_2000', 'z': 10., 'v_obs': 0.}]
    rows = []
    for cell, offset in [('620_constant', 0), ('620_early_up', 2)]:
        for i, seed in enumerate(R.SEEDS):
            rows.append({'cell_id': cell, 'seed': seed, 'x_2000': 8+i+offset, 'error': ''})
    out = R.summarise(list(reversed(rows)), targets)
    changed = out['cells'][1]
    assert changed['scores']['0.1']['imax'] == pytest.approx(2/np.sqrt(2.5*1.2+1))
    assert changed['paired_difference_mean']['x'] == 2
    assert changed['paired_difference_se']['x'] == 0
    for bad in (rows[:-1], rows+[rows[-1]], [{**rows[0], 'error': 'fail'}]+rows[1:]):
        with pytest.raises(ValueError):
            R.summarise(bad, targets)
