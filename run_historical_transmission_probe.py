#!/usr/bin/env python3
"""Prepare a bounded transmission-shape diagnostic. Default: zero simulations.

--smoke: three paired production/neutral checks (6 simulations), no fitting.
--run-diagnostic: 2 anchors x 5 fixed profiles x 5 seeds (50 simulations).
No search, target changes, NROY updates, automatic continuation or 1990 restart.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

import run_joint_fit_comparison_20260917 as previous
from historical_transmission_probe import (
    ContactSchedule, HistoricalProbeEngine, build_profiles, check_initialisation_contract,
)

ROOT = Path(__file__).resolve().parent
SEEDS = tuple(range(196000, 196005))
SMOKE_SEEDS = (196100, 196101, 196102)
DEFAULT_OUTPUT = ROOT / 'outputs/calibration/historical_transmission_track_20260917'
PLAN = ROOT / 'configs/hm_wave_plan_focused_diagnoses_then_aids_20260916.json'
CONTRACT = ROOT / 'configs/initialisation_1990_readiness.json'


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def prepare():
    """Only reads existing artifacts. No engine construction or simulation."""
    inputs = previous.load_inputs()
    orient = next(p for p in inputs['space']['parameters'] if p['name'] == 'orient_msm')
    cells = []
    for anchor, params in ((620, inputs['anchor_a']), (548, inputs['anchor_b'])):
        profiles = build_profiles(params['orient_msm'], orient['min'], orient['max'])
        for name, schedule in profiles.items():
            cell = {'cell_id': f'{anchor}_{name}', 'anchor': anchor, 'profile': name,
                    'params': params, 'schedule': schedule.as_dict()}
            config = previous.cell_config(cell, inputs['base_config'], inputs['space'])
            previous.assert_stochastic_msm_config(config, cell['cell_id'])
            cells.append(cell)
    import hm_target_register as tr
    plan = json.loads(PLAN.read_text())
    register = tr.load_register(inputs['paths']['target_register'])
    targets = register.restrict_years(
        [t for t in register.calibration_targets() if t.series_name in plan['scored_years']],
        plan['scored_years'])
    if len(targets) != 15:
        raise ValueError('expected unchanged 15-target focused contract')
    target_rows = [{'key': t.key, 'column': f'{t.model_output}_{t.year}',
                    'z': float(t.z), 'v_obs': float(t.v_obs.variance)} for t in targets]
    paths = [*inputs['paths'].values(), PLAN, CONTRACT,
             Path(previous.__file__), Path(__file__), ROOT / 'historical_transmission_probe.py',
             previous.SOURCE / 'manifest.json', previous.SOURCE / 'wave_01/evaluated.csv',
             ROOT / 'tests/test_historical_transmission_probe.py']
    # Freeze all live top-level simulator/support modules, not just git HEAD.
    paths += sorted(ROOT.glob('*.py'))
    workbook = Path(register.source_workbook) if hasattr(register, 'source_workbook') else ROOT.parent / 'data/raw/validation/DiseaseData.xlsx'
    if not workbook.is_absolute():
        workbook = ROOT / workbook
    paths.append(workbook)
    fingerprints = {str(p.resolve()): previous.sha256(p) for p in paths}
    report = {
        'status': 'PREPARED_NOT_CALIBRATED', 'simulations_started': 0,
        'planned_diagnostic_simulations': len(cells) * len(SEEDS),
        'neutral_gate_simulations': 2 * len(SMOKE_SEEDS),
        'maximum_simulations_per_diagnostic_command': len(cells)*len(SEEDS) + 2*len(SMOKE_SEEDS),
        'seeds': list(SEEDS), 'smoke_seeds': list(SMOKE_SEEDS), 'cells': cells,
        'targets': target_rows, 'input_sha256': fingerprints,
        'design': '25% of distance toward existing orient_msm bounds; linear diagnostic windows',
        'interpretation': 'local fixed-profile sensitivity, not a fitted comparison or proof of feasibility',
        'initialisation_1990': check_initialisation_contract(json.loads(CONTRACT.read_text())),
        'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'git_status': subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
    }
    return report, inputs


def simulate(payload):
    from calib_extractor import extract_annual_outputs
    started = time.monotonic()
    row = {k: payload[k] for k in ('task_id', 'cell_id', 'anchor', 'seed')}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            engine = HistoricalProbeEngine(copy.deepcopy(payload['config']), seed=payload['seed'],
                                           contact_schedule=ContactSchedule(**payload['schedule']))
            engine.output_writer.enabled = False
            result = engine.run()
        outputs = extract_annual_outputs(result, 1975, 2025)
        for indicator, series in outputs.items():
            for year, value in series.items():
                row[f'{indicator}_{year}'] = float(value)
        if set(engine.probe_states) != {'1990', '2025'}:
            raise ValueError('missing exact start-of-year state capture')
        arrays = {f'{year}_{name}': state[name] for year, state in engine.probe_states.items()
                  for name in ('population', 'childhood_population')}
        folder = Path(payload['state_dir'])
        stem = f"{payload['cell_id']}_seed{payload['seed']}"
        np.savez_compressed(folder / f'{stem}.npz', **arrays)
        dump(folder / f'{stem}.json', {
            'summaries': {y: s['summary'] for y, s in engine.probe_states.items()},
            'axes': ['age', 'orientation', 'activity', 'hiv', 'diagnosis', 'art', 'prep'],
            'orientations': list(engine.groups.orientations),
            'age_groups': list(engine.groups.age_groups),
            'activities': list(engine.groups.activities),
            'diagnosis_axis': ['undiagnosed', 'diagnosed'],
            'art_axis': ['off_art', 'on_art'],
            'hiv_indices': engine.groups.hiv_to_idx,
            'contact_trace': engine.contact_trace,
            'restart_safe': False,
            'warning': 'Model-derived stocks, not observations. Residence queues/RNG and other state are not captured.',
        })
        row['error'] = ''
    except Exception as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
    row['wall_seconds'] = time.monotonic() - started
    return row


def smoke(inputs):
    """Compare whole trajectories and terminal population for 3 shared seeds."""
    from run_v3 import SimulationEngineV3
    params = inputs['anchor_a']
    config = previous.cell_config({'cell_id': 'neutral', 'params': params}, inputs['base_config'], inputs['space'])
    orient = next(p for p in inputs['space']['parameters'] if p['name'] == 'orient_msm')
    neutral = build_profiles(params['orient_msm'], orient['min'], orient['max'])['constant']
    results = []
    for seed in SMOKE_SEEDS:
        with contextlib.redirect_stdout(io.StringIO()):
            base = SimulationEngineV3(copy.deepcopy(config), seed=seed)
            probe = HistoricalProbeEngine(copy.deepcopy(config), seed=seed, contact_schedule=neutral)
            base.output_writer.enabled = probe.output_writer.enabled = False
            a, b = base.run(), probe.run()
        pd.testing.assert_frame_equal(a['summary'], b['summary'], check_exact=True)
        np.testing.assert_array_equal(base.state.population, probe.state.population)
        np.testing.assert_array_equal(base.demography.childhood.population, probe.demography.childhood.population)
        assert set(probe.probe_states) == {'1990', '2025'}
        results.append({'seed': seed, 'all_output_columns_identical': True,
                        'adult_and_childhood_final_stocks_identical': True})
        print(f'neutral equivalence: seed {seed} passed', flush=True)
    return {'status': 'PASS', 'simulations': 2 * len(SMOKE_SEEDS), 'checks': results}


def summarise(rows, targets):
    """Preserve predictive-local scoring; no promotion to a calibration ensemble."""
    if any(r.get('error') for r in rows):
        raise ValueError('cannot score failed simulations')
    records = []
    grouped = {}
    for r in rows:
        grouped.setdefault(r['cell_id'], []).append(r)
    z = np.array([t['z'] for t in targets])
    obs_var = np.array([t['v_obs'] for t in targets])
    for cell_id, group in sorted(grouped.items()):
        if sorted(r['seed'] for r in group) != list(SEEDS):
            raise ValueError('incomplete or duplicated replicate set')
        group = sorted(group, key=lambda r: r['seed'])
        arr = np.array([[r[t['column']] for t in targets] for r in group], dtype=float)
        if not np.isfinite(arr).all() or (arr < 0).any():
            raise ValueError('invalid target outputs')
        mean, variance = arr.mean(0), arr.var(0, ddof=1)
        scores = {}
        for fraction in (0.1, 0.2):
            values = abs(mean-z) / np.sqrt(obs_var + variance*(1+1/len(group)) + (fraction*z)**2)
            scores[str(fraction)] = {'imax': float(values.max()), 'binding': targets[values.argmax()]['key'],
                                    'all_anchors_below_3_descriptive': bool((values <= 3).all())}
        baseline = sorted(grouped[cell_id.split('_')[0] + '_constant'], key=lambda r: r['seed'])
        base_arr = np.array([[r[t['column']] for t in targets] for r in baseline])
        paired = arr-base_arr
        records.append({'cell_id': cell_id, 'n': len(group), 'scores': scores,
                        'means': dict(zip([t['key'] for t in targets], mean.tolist())),
                        'paired_difference_mean': dict(zip([t['key'] for t in targets], paired.mean(0).tolist())),
                        'paired_difference_se': dict(zip([t['key'] for t in targets], (paired.std(0, ddof=1)/np.sqrt(len(group))).tolist()))})
    return {'status': 'DIAGNOSTIC_ONLY', 'cells': records,
            'stop': 'No automatic follow-up, bound changes, calibrated-ensemble claim or 1990 restart.'}


def write_results_markdown(path, summary, targets):
    lines = ['# Fixed-profile historical transmission diagnostic', '',
             'Five stochastic seeds per cell; paired differences use the same seeds.',
             'Local sensitivity only. No model has been fitted and no NROY set is created.', '',
             '| Cell | Primary Imax | Binding target | 20% sensitivity Imax |',
             '|---|---:|---|---:|']
    for c in summary['cells']:
        a, b = c['scores']['0.1'], c['scores']['0.2']
        lines.append(f"| {c['cell_id']} | {a['imax']:.3f} | {a['binding']} | {b['imax']:.3f} |")
    for c in summary['cells']:
        lines += ['', f"## {c['cell_id']}", '',
                  '| Target | Observed | Mean | Paired change vs constant | SE of paired change |',
                  '|---|---:|---:|---:|---:|']
        for t in targets:
            k = t['key']
            lines.append(f"| {k} | {t['z']:g} | {c['means'][k]:.1f} | "
                         f"{c['paired_difference_mean'][k]:+.1f} | {c['paired_difference_se'][k]:.2f} |")
    lines += ['', '## Decision boundary', '',
              'Check early AIDS, middle diagnoses and recent AIDS together. Inspect the saved',
              '1990/2025 joint state tensors before inferring anything about a policy baseline.',
              'A good local change motivates a separately specified fit; a bad change rules out',
              'only these perturbations at these two anchors. Neither proves a structural cause.',
              'Stop here: no automatic extra runs, widened bounds or 1990 restart.']
    Path(path).write_text('\n'.join(lines)+'\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--validate-only', action='store_true')
    mode.add_argument('--prepare', action='store_true', help='write frozen design; zero simulations')
    mode.add_argument('--smoke', action='store_true')
    mode.add_argument('--run-diagnostic', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 8:
        raise ValueError('workers must be between 1 and 8')
    report, inputs = prepare()
    if not (args.prepare or args.smoke or args.run_diagnostic):
        print(json.dumps({k: v for k, v in report.items() if k not in ('git_status', 'input_sha256', 'cells')}, indent=2))
        return 0
    previous._assert_unoccupied(args.output_dir)
    if args.smoke or args.run_diagnostic:
        # Fresh seeds for scientific diagnostics; fixed regression seeds may
        # intentionally repeat because the neutral gate is an equivalence test.
        if args.run_diagnostic:
            report['diagnostic_seed_check'] = previous.assert_seeds_disjoint(SEEDS, ROOT / 'outputs')
        gate = previous.run_diagnosis_test_gate()
        if not gate['ok']:
            raise ValueError(f'existing diagnosis gate failed: {gate}')
        report['diagnosis_gate'] = gate  # explicit existing deterministic exception retained
    args.output_dir.mkdir(parents=True, exist_ok=True)
    dump(args.output_dir / 'design.json', report)
    if args.prepare:
        print(f'Prepared {args.output_dir}; 0 simulations started.')
        return 0
    if args.smoke:
        result = smoke(inputs)
        dump(args.output_dir / 'SMOKE_RESULT.json', result)
        previous.write_manifest(args.output_dir, {'status': 'COMPLETE', 'seeds': list(SMOKE_SEEDS), 'kind': 'neutral_equivalence'})
        return 0
    # Require an explicit passing neutral gate under the CURRENT source hashes.
    # Executed here rather than trusting a potentially stale smoke artifact.
    result = smoke(inputs)
    dump(args.output_dir / 'SMOKE_RESULT.json', result)
    state_dir = args.output_dir / 'states'
    state_dir.mkdir()
    cells = {c['cell_id']: c for c in report['cells']}
    configs = {k: previous.cell_config(c, inputs['base_config'], inputs['space']) for k, c in cells.items()}
    tasks = previous.build_tasks(report['cells'], SEEDS)
    def payload(task):
        return {**task, 'config': configs[task['cell_id']],
                'schedule': cells[task['cell_id']]['schedule'], 'state_dir': str(state_dir)}
    result = previous.run_tasks(tasks, payload, args.output_dir, report, workers=args.workers, worker=simulate,
                                max_projected_hours=0.5)
    if result['status'] == 'COMPLETE':
        summary = summarise(result['rows'], report['targets'])
        dump(args.output_dir / 'RESULTS.json', summary)
        write_results_markdown(args.output_dir / 'RESULTS.md', summary, report['targets'])
    previous.write_manifest(args.output_dir, {'status': result['status'], 'seeds': list(SEEDS),
                            'kind': 'fixed_profile_diagnostic', 'elapsed_seconds': result['elapsed_seconds']})
    return 0 if result['status'] == 'COMPLETE' else 1


if __name__ == '__main__':
    raise SystemExit(main())
