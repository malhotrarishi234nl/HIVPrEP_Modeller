"""Bounded fresh-seed confirmation; no new wave and no simulator changes."""
import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import hm_target_register as tr
import run_history_matching as hm
from run_hm_confirmation import (
    _verified_manifest, _check_source_compatibility, _confirmation_worker,
    aggregate_replicates, _write_manifest, _sha256,
)

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'outputs/calibration/hm_focused_diagnoses_then_aids_20260916'
OUTPUT = ROOT / 'outputs/calibration/hm_focused_confirmation_600_621_20260917'


def target_diagnostics(raw, means, targets):
    """Descriptive trajectory agreement; never an additional acceptance rule."""
    records = []
    for _, mean in means.iterrows():
        group = raw.loc[raw.source_design_id == int(mean.design_id)]
        simultaneous = np.ones(len(group), dtype=bool)
        for t in targets:
            col = f'{t.model_output}_{t.year}'
            values = group[col].to_numpy(float)
            if not np.isfinite(values).all():
                raise ValueError(f'Nonfinite trajectory for {t.key}')
            within = np.abs(values-t.z) <= 0.2*abs(t.z)
            simultaneous &= within
            records.append({'design_id': int(mean.design_id), 'target': t.key,
                            'observed': t.z, 'mean': mean[col],
                            'sd': np.std(values, ddof=1), 'p05': np.quantile(values, .05),
                            'median': np.median(values), 'p95': np.quantile(values, .95),
                            'fraction_within_20pct': within.mean()})
        records.append({'design_id': int(mean.design_id), 'target': 'ALL_TARGETS_SAME_SEED',
                        'fraction_within_20pct': simultaneous.mean()})
    return pd.DataFrame(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--replicates', type=int, default=50)
    parser.add_argument('--seed', type=int, default=194000)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if args.workers < 1 or args.replicates < 5 or args.seed < 0:
        raise ValueError('Need positive workers, >=5 replicates and nonnegative seed')
    manifest = _verified_manifest(SOURCE)
    provenance = manifest['input_provenance']
    paths = {key: Path(provenance[key]['path']) for key in ('base_config', 'calib_space', 'target_register')}
    _check_source_compatibility(manifest, base_config_path=paths['base_config'],
                                calib_space_path=paths['calib_space'],
                                register_path=paths['target_register'], v_ensemble_path=None)
    wave = SOURCE / 'wave_01'
    evaluated = pd.read_csv(wave/'evaluated.csv')
    eligible = pd.read_csv(wave/'nroy_members.csv')
    ids = [600, 621]
    if not set(ids) <= set(eligible.design_id):
        raise ValueError('Declared vectors are not source primary survivors')
    base = json.loads(paths['base_config'].read_text())
    space = json.loads(paths['calib_space'].read_text())
    names = [p['name'] for p in space['parameters']]
    selected = evaluated.loc[evaluated.design_id.isin(ids), ['design_id', *names]].copy()
    if len(selected) != 2:
        raise ValueError('Expected exactly two unique vectors')
    settings = manifest['run_settings']
    seeds = list(range(args.seed, args.seed+args.replicates))
    old_seeds = {settings['simulation_seed']+i*settings.get('replicate_seed_stride', 1)
                 for i in range(settings['replicates_per_design_point'])}
    if old_seeds.intersection(seeds):
        raise ValueError('Fresh seeds overlap source seeds')
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise ValueError(f'Refusing nonempty output: {OUTPUT}')
    register = tr.load_register(paths['target_register'])
    series = ['shm_new_diagnoses_msm', 'rivm_aids_msm']
    targets = register.restrict_years([t for t in register.calibration_targets()
                                     if t.series_name in series], manifest['scored_years'])
    if len(targets) != 15:
        raise ValueError('Expected seven diagnosis and eight AIDS anchors')
    validation = {'status': 'VALID', 'design_ids': ids, 'replicates': args.replicates,
                  'seeds': [seeds[0], seeds[-1]], 'simulations': 2*args.replicates,
                  'variance': 's(x)*(1+1/K)', 'source_manifest_sha256': _sha256(SOURCE/'manifest.json')}
    print(json.dumps(validation, indent=2), flush=True)
    if args.validate_only:
        return
    OUTPUT.mkdir(parents=True)
    selected.to_csv(OUTPUT/'selected_parameters.csv', index=False)
    tasks = []
    for _, row in selected.iterrows():
        for seed in seeds:
            tasks.append({'design_id': len(tasks), 'source_design_id': int(row.design_id),
                          'params': {name: float(row[name]) for name in names},
                          'base_config': base, 'calib_space': space, 'seed': seed,
                          'replicate_seeds': [seed], 'deterministic_calibration': False,
                          'simulation_start_year': settings['simulation_start_year'],
                          'scoring_start_year': settings['scoring_start_year']})
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(_confirmation_worker, t) for t in tasks]):
            rows.append(future.result())
            if len(rows) % 10 == 0 or len(rows) == len(tasks):
                pd.DataFrame(rows).to_csv(OUTPUT/'raw_replicates.partial.csv', index=False)
                print(f'Confirmation completed {len(rows)}/{len(tasks)} simulations', flush=True)
    raw = pd.DataFrame(rows).sort_values(['source_design_id', 'confirmation_seed'])
    raw.to_csv(OUTPUT/'raw_replicates.csv', index=False)
    if raw.error.fillna('').astype(str).str.strip().ne('').any():
        raise RuntimeError('Simulation errors; inspect raw_replicates.csv. No acceptance result produced.')
    means = aggregate_replicates(raw, selected, names)
    means.to_csv(OUTPUT/'confirmed_means.csv', index=False)
    summaries = []
    for block, wanted in [('diagnoses', series[:1]), ('joint', series)]:
        target_diagnostics(raw, means, [t for t in targets if t.series_name in wanted]).to_csv(
            OUTPUT/f'trajectory_diagnostics_{block}.csv', index=False)
        for allowance in (.1, .2):
            score = hm.score_wave(means, register, wanted, disc_multiplier=allowance,
                                  disc_method='relative_to_target', ensemble_mode='predictive_local',
                                  replicates=args.replicates, scored_years=manifest['scored_years'])
            stability = {k: v for k, v in score['variance_stability'].items() if k != 'unstable_mask'}
            (OUTPUT/f'variance_stability_{block}_{allowance}.json').write_text(json.dumps(stability, indent=2)+'\n')
            for i, row in means.iterrows():
                summaries.append({'design_id': int(row.design_id), 'block': block,
                                  'discrepancy': allowance, 'i_max': float(score['combined'].i_max[i]),
                                  'binding_target': score['combined'].binding_target[i],
                                  'retained': bool(score['mask'][i])})
            pd.DataFrame(score['matrix'], index=means.design_id,
                         columns=[t.key for t in score['targets']]).to_csv(
                OUTPUT/f'implausibility_{block}_{allowance}.csv')
    pd.DataFrame(summaries).to_csv(OUTPUT/'confirmation_summary.csv', index=False)
    lines = ['# Fresh-seed confirmation', '', 'Predictive variance retained: s(x) + s(x)/K.',
             'No new wave or region update. 20% trajectory windows are descriptive, not acceptance criteria.',
             '', '| ID | Block | Discrepancy | I max | Retained |', '|---|---|---|---|---|']
    lines += [f"| {r['design_id']} | {r['block']} | {r['discrepancy']} | {r['i_max']:.3f} | {r['retained']} |" for r in summaries]
    (OUTPUT/'RUN_SUMMARY.md').write_text('\n'.join(lines)+'\n')
    _write_manifest(OUTPUT, {**validation, 'status': 'COMPLETE',
                            'generated_utc': datetime.now(timezone.utc).isoformat(),
                            'runtime': hm.repository_provenance(),
                            'script_sha256': _sha256(Path(__file__)),
                            'confirmation_helper_sha256': _sha256(ROOT/'run_hm_confirmation.py')})
    print('\n'.join(lines), flush=True)


if __name__ == '__main__':
    main()
