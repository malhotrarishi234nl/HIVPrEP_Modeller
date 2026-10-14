#!/usr/bin/env python3
"""One-simulation engineering check of the non-neutral adapter and file writer.

Not a scientific experiment. The fixed regression seed intentionally repeats.
Default is validation only; --run performs one full trajectory.
"""
import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run_historical_transmission_probe as R
from historical_transmission_probe import ContactSchedule, HistoricalProbeEngine
from run_v3 import SimulationEngineV3


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', action='store_true')
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    report, inputs = R.prepare()
    cell = next(c for c in report['cells'] if c['cell_id'] == '620_early_up')
    config = R.previous.cell_config(cell, inputs['base_config'], inputs['space'])
    if not args.run:
        print('Validated; 0 simulations started. --run executes one engineering check.')
        return
    R.previous._assert_unoccupied(args.output_dir)
    with contextlib.redirect_stdout(io.StringIO()):
        base = SimulationEngineV3(copy.deepcopy(config), seed=196200)
        varied = HistoricalProbeEngine(copy.deepcopy(config), seed=196200,
                                        contact_schedule=ContactSchedule(**cell['schedule']))
    base._current_year = varied._current_year = 1990.
    a = base._step_contacts_and_mixing()
    b = varied._step_contacts_and_mixing()
    i = base.groups.orient_to_idx['MSM']
    factor = varied.contact_schedule.factor(1990.)
    assert factor > 1 and a[i, :, i, :].sum() > 0
    np.testing.assert_allclose(b[i, :, i, :], a[i, :, i, :] * factor, rtol=0, atol=0)
    mask = np.ones(a.shape, dtype=bool); mask[i, :, i, :] = False
    np.testing.assert_array_equal(a[mask], b[mask])
    folder = args.output_dir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'states').mkdir()
    report['writer_check_script_sha256'] = R.previous.sha256(Path(__file__))
    R.dump(folder / 'design.json', report)
    row = R.simulate({'task_id': 0, 'cell_id': cell['cell_id'], 'anchor': 620, 'seed': 196200,
                     'config': config, 'schedule': cell['schedule'], 'state_dir': str(folder / 'states')})
    assert row['error'] == '', row['error']
    for t in report['targets']:
        assert np.isfinite(row[t['column']])
    with np.load(folder / 'states/620_early_up_seed196200.npz') as state:
        assert set(state.files) == {'1990_population', '1990_childhood_population',
                                   '2025_population', '2025_childhood_population'}
        for year in ('1990', '2025'):
            assert state[year + '_population'].ndim == 7
            assert (state[year + '_population'] >= 0).all()
    meta = json.loads((folder / 'states/620_early_up_seed196200.json').read_text())
    assert meta['restart_safe'] is False
    R.dump(folder / 'CHECK_RESULT.json', {'status': 'PASS', 'simulations': 1, 'seed': 196200,
        'contact_factor': factor, 'non_msm_contacts_unchanged': True,
        'all_15_targets_present': True, 'both_joint_state_tensors_roundtrip': True,
        'kind': 'engineering_integration_check_not_scientific_evidence'})
    R.previous.write_manifest(folder, {'status': 'COMPLETE', 'seeds': [196200],
                                     'kind': 'engineering_integration_check'})
    print('PASS: real contact path, target outputs and start-of-year state files.')


if __name__ == '__main__':
    main()
