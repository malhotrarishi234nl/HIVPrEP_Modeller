#!/usr/bin/env python3
"""Verify completed diagnostic artifacts and independently recompute key results.

Writes a new review directory; never modifies the experiment or runs simulations.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError('review output directory must be empty')
    manifest = json.loads((args.source/'manifest.json').read_text())
    if manifest['status'] != 'COMPLETE':
        raise ValueError('only a complete diagnostic may be reviewed')
    for artifact in manifest['artefacts']:
        path = args.source/artifact['path']
        assert sha(path) == artifact['sha256'], str(path)
        assert path.stat().st_size == artifact['bytes']
    design = json.loads((args.source/'design.json').read_text())
    results = json.loads((args.source/'RESULTS.json').read_text())
    with (args.source/'raw_seed_outputs.partial.csv').open() as handle:
        raw = list(csv.DictReader(handle))
    assert len(raw) == design['planned_diagnostic_simulations'] == 50
    assert all(not r['error'] for r in raw)
    targets = design['targets']
    observed = np.array([t['z'] for t in targets])
    obs_var = np.array([t['v_obs'] for t in targets])
    keys = [t['key'] for t in targets]
    saved = {c['cell_id']: c for c in results['cells']}
    computed = []
    for cell in design['cells']:
        cid = cell['cell_id']
        rows = sorted([r for r in raw if r['cell_id']==cid], key=lambda r: int(r['seed']))
        assert [int(r['seed']) for r in rows] == design['seeds']
        values = np.array([[float(r[t['column']]) for t in targets] for r in rows])
        assert np.isfinite(values).all()
        means = values.mean(0)
        score = abs(means-observed)/np.sqrt(obs_var+values.var(0,ddof=1)*(1+1/len(rows))+(observed*.1)**2)
        np.testing.assert_allclose(means,[saved[cid]['means'][k] for k in keys],rtol=1e-12,atol=1e-12)
        np.testing.assert_allclose(score.max(),saved[cid]['scores']['0.1']['imax'],rtol=1e-12)
        states = []
        for row in rows:
            state=json.loads((args.source/'states'/f"{cid}_seed{row['seed']}.json").read_text())
            assert state['restart_safe'] is False
            hiv_indices=state['hiv_indices']
            infected=[v for k,v in hiv_indices.items() if k!='Susceptible']
            summary={}
            for year in ('1990','2025'):
                p=np.array(state['summaries'][year]['msm_hiv_dx_art'])
                summary[year]={'plhiv':float(p[infected].sum()),
                               'undiagnosed':float(p[infected,0,:].sum()),
                               'on_art':float(p[infected,:,1].sum()),
                               'aids':float(p[hiv_indices['AIDS']].sum())}
            states.append(summary)
        mean_states={year:{name:float(np.mean([s[year][name] for s in states]))
                           for name in states[0][year]} for year in ('1990','2025')}
        computed.append({'cell_id':cid,'means':dict(zip(keys,means.tolist())),
                         'primary_imax':float(score.max()),'binding':keys[score.argmax()],
                         'mean_start_of_year_msm_stocks':mean_states})
    lines=['# Verified fixed-profile diagnostic results','',
           '50/50 successful simulations; five paired seeds per cell. All source artifact hashes verified.',
           'Target means and primary scores independently recomputed from raw replicates.',
           'Local sensitivity only: no calibration search, posterior or validated baseline.','',
           '| Cell | AIDS 1990 | Dx 2000 | Dx 2004 | AIDS 2022 | Primary Imax | Binding |',
           '|---|---:|---:|---:|---:|---:|---|']
    selected=['rivm_aids_msm@1990','shm_new_diagnoses_msm@2000','shm_new_diagnoses_msm@2004','rivm_aids_msm@2022']
    lines.append('| Observed | '+' | '.join(f'{observed[keys.index(k)]:g}' for k in selected)+' | — | — |')
    for c in computed:
        lines.append('| '+c['cell_id']+' | '+' | '.join(f"{c['means'][k]:.1f}" for k in selected)+f" | {c['primary_imax']:.3f} | {c['binding']} |")
    lines += ['', '## Model-derived MSM stocks at the start of 2025','',
              'These are predictions to examine, not observed/validated initialisation inputs.','',
              '| Cell | PLHIV | Undiagnosed | On ART | AIDS |','|---|---:|---:|---:|---:|']
    for c in computed:
        s=c['mean_start_of_year_msm_stocks']['2025']
        lines.append('| '+c['cell_id']+' | '+' | '.join(f'{s[k]:.1f}' for k in ('plhiv','undiagnosed','on_art','aids'))+' |')
    lines += ['', '## Limits','',
              'Early perturbations increase the effective multiplier by approximately 76–79%;',
              'middle/recent decreases are only about 4–5% because the anchors are near the lower bound.',
              'Five seeds give imprecise local variance estimates. A negative result rules out neither',
              'all temporal schedules nor the current model. No automatic follow-up run is implied.',
              'The 1990 tensors remain incomplete restart checkpoints and model-derived stocks.']
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/'VERIFIED_RESULTS.md').write_text('\n'.join(lines)+'\n')
    (args.output_dir/'verification.json').write_text(json.dumps({
        'status':'VERIFIED','source_manifest_sha256':sha(args.source/'manifest.json'),
        'review_script_sha256':sha(Path(__file__)), 'simulations':50,'cells':computed},indent=2)+'\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
