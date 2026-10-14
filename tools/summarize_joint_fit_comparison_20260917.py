"""Read-only numerical audit of the completed 90-run experiment; JSON to stdout.

Run from the repository root. No simulations or output-file writes.
Intervals are exploratory paired t intervals on seed-level log ratios, not
equivalence tests, simultaneous intervals, or uncertainty in observed targets.
"""
import csv
import hashlib
import itertools
import json
from pathlib import Path
import sys

import numpy as np
from scipy.stats import t

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import hm_target_register as tr


def main():
    folder = ROOT / 'outputs/calibration/hm_joint_fit_comparison_20260917'
    manifest = json.loads((folder / 'manifest.json').read_text())
    assert manifest['status'] == 'COMPLETE'
    for artifact in manifest['artefacts']:
        content = (folder / artifact['path']).read_bytes()
        assert len(content) == artifact['bytes']
        assert hashlib.sha256(content).hexdigest() == artifact['sha256']
    spec = json.loads((folder / 'frozen_spec.json').read_text())
    with (folder / 'raw_seed_outputs.csv').open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 90
    assert len({(r['cell_id'], r['seed']) for r in rows}) == 90
    assert all(not r['error'] and r['engine_deterministic'] == 'False'
               and r['msm_relative_diagnosis_path'] == 'True' for r in rows)
    plan = json.loads((ROOT / 'configs/hm_wave_plan_focused_diagnoses_then_aids_20260916.json').read_text())
    register = tr.load_register(ROOT / 'configs/hm_target_register_focused_20260916.json')
    targets = register.restrict_years([x for x in register.calibration_targets()
                                     if x.series_name in plan['scored_years']], plan['scored_years'])
    assert len(targets) == 15
    columns = [f'{x.model_output}_{x.year}' for x in targets]
    observed = np.array([x.z for x in targets])
    obs_variance = np.array([x.v_obs.variance for x in targets])
    grouped = {cell['cell_id']: sorted([r for r in rows if r['cell_id'] == cell['cell_id']],
                                     key=lambda r: int(r['seed'])) for cell in spec['cells']}
    for group in grouped.values():
        assert [int(r['seed']) for r in group] == spec['seeds']
    arrays = {cell: np.array([[float(r[c]) for c in columns] for r in group])
              for cell, group in grouped.items()}
    assert all(np.isfinite(a).all() and (a > 0).all() for a in arrays.values())
    baseline = arrays['A_000']
    baseline_mean = baseline.mean(0)
    aids_index = columns.index('aids_first_diagnosis_proxy_msm_1990')
    dx_indices = [columns.index(f'new_diagnoses_msm_{year}') for year in (2000, 2004)]
    result = {'source_manifest_sha256': hashlib.sha256((folder / 'manifest.json').read_bytes()).hexdigest(),
              'simulations': len(rows), 'elapsed_seconds': manifest['elapsed_seconds'], 'cells': {}}
    for cell, array in arrays.items():
        mean = array.mean(0)
        lr = np.log(array / baseline)
        half_width = t.ppf(.975, 9) * lr.std(0, ddof=1) / np.sqrt(10)
        scores = {}
        for fraction in (.1, .2):
            score = abs(mean - observed) / np.sqrt(obs_variance + array.var(0, ddof=1)*1.1 + (fraction*observed)**2)
            scores[str(fraction)] = {'imax': float(score.max()), 'binding': targets[score.argmax()].key}
        result['cells'][cell] = {
            'means': dict(zip([x.key for x in targets], mean.tolist())), 'scores': scores,
            'paired_geometric_ratios_and_95pct_intervals': {
                target.key: np.exp([lr.mean(0)[i], lr.mean(0)[i]-half_width[i], lr.mean(0)[i]+half_width[i]]).tolist()
                for i, target in enumerate(targets)},
            'R1a': bool(mean[aids_index] >= observed[aids_index]/2 and
                        all(.5 <= mean[i]/observed[i] <= 2 for i in dx_indices)),
            'factor_two_window_exits': [targets[i].key for i in range(15)
                if .5 <= baseline_mean[i]/observed[i] <= 2 and not .5 <= mean[i]/observed[i] <= 2],
            'max_absolute_log_error': float(abs(np.log(mean/observed)).max())}
    # R1a fails in all factorial cells; R1b cannot rescue it. F1-only and
    # F2-only also fail the prespecified 1.5x R2 recognition increase.
    assert not any(result['cells'][c]['R1a'] for c in arrays if c.startswith('A_'))
    pre = [c for c in rows[0] if c.rsplit('_', 1)[-1].isdigit()
           and 1975 <= int(c.rsplit('_', 1)[-1]) < 1985]
    assert all(float(a[c]) == float(b[c]) or (np.isnan(float(a[c])) and np.isnan(float(b[c])))
               for a, b in zip(grouped['A_100'], grouped['A_000']) for c in pre)
    result['pre1985_F1_identity'] = True
    result['accounting_all_orientations_1985_through_1990'] = {}
    for cell in ('A_000', 'B_000'):
        residuals = [float(r['aids_undiagnosed_start_startofyear_1985'])
                     + sum(float(r[f'aids_progression_while_undiagnosed_{y}'])
                           - float(r[f'hiv_diagnoses_in_aids_stage_{y}']) for y in range(1985, 1991))
                     - float(r['aids_undiagnosed_start_startofyear_1991']) for r in grouped[cell]]
        result['accounting_all_orientations_1985_through_1990'][cell] = float(np.mean(residuals))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
