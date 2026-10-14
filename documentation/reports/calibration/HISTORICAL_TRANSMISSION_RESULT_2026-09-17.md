# Historical transmission diagnostic — completed decision

> Archived under `documentation/reports/calibration/`; original filename retained.

**Status:** 50/50 scientific diagnostic simulations completed without errors,
plus six neutral-equivalence regression simulations. Five shared fresh diagnostic
seeds per cell, two reference parameter vectors, five fixed profiles. Diagnostic
runtime excluding gates: 166.8 seconds. This is a local sensitivity experiment,
not a fitted-model comparison or an accepted calibration ensemble.

## Decision

**Do not promote any tested temporal profile to a baseline. Stop this fixed-profile
experiment here.** The hypothesis of useful historical transmission flexibility
remains open, but the experiment does not demonstrate a jointly compatible region.
It does not prove the current model or all temporal schedules incapable of fitting.
Do not add a presentation mechanism or switch to a 1990 start on the strength of
this negative result.

The next scientific deliverable should be the reduced-parameter **joint fitting
specification**, with the current model as an adequately fitted comparator. It
must identify which transmission-scale parameters are redundant/fixed, define
allowed temporal shape and bounds from evidence or explicit assumptions, and
state how diagnosis/initial-state uncertainty is retained. Do not run another
ad hoc collection of stronger versions of these profiles. No such fitting run
or new bound has been implemented or launched here.

## Results

Means across five seeds; observed targets unchanged:

| Setting at reference 620 | AIDS 1990 | MSM diagnoses 2000 | MSM diagnoses 2004 | AIDS 2022 |
|---|---:|---:|---:|---:|
| Observed | 318 | 360 | 572 | 57 |
| Constant contacts | 81.6 | 481.8 | 760.6 | 89.4 |
| Early increase | 518.0 | 6752.4 | 8050.6 | 615.0 |
| Middle decrease | 81.6 | 452.8 | 700.0 | 82.4 |
| Recent decrease | 81.6 | 481.6 | 753.6 | 79.6 |
| Combined changes | 518.0 | 6529.2 | 7641.0 | 568.2 |

The second reference gives the same qualitative warning: its early increase
raises AIDS 1990 from 364.0 to 3079.6 and diagnoses 2000 from 1620.8 to 4987.8.
Its middle decrease modestly reduces diagnoses 2000 to 1515.4.

Zero of ten cells passes the unchanged primary Imax <= 3 rule; zero passes the
20% discrepancy sensitivity. The lowest primary Imax is 6.991; the lowest
sensitivity Imax is 3.658. Both bind on AIDS 1990 in reference-620 profiles without
the early increase. Do not interpret larger Imax as an effect size: local
estimated variances enter the denominator.

**Well-supported local finding:** increasing early contact supply is a powerful
lever, but these fixed increases carry a large excess into subsequent years.
Modest later decreases do not offset it. This establishes sensitivity to the
intervention, not the correct historical trajectory or the need for new structure.

**Important design limitation:** the changes are asymmetric. Early levels rise
about 76–79%, while middle/recent levels fall only about 4–5%, because both anchors
are close to the existing lower bound. This experiment cannot rule out smaller
early increases, different timing, or stronger independently justified decreases.
The chosen windows and perturbation fraction are engineering choices, not Dutch
behavioural estimates. All other parameters were held fixed, so interactions with
re-fitted initial conditions/diagnosis were not estimated.

## Why target counts are insufficient

The mean model-derived MSM PLHIV stock at the start of 2025 for reference 620 is:

- Constant: 14,447.4.
- Early increase: 110,687.6.
- Combined: 103,981.8.

This is a large persistent change in the population to which future prevention
would apply. It is not merely altered reporting. These are predictions, not
observed stock targets or a completed burden validation. They reinforce the need
to assess baseline stocks together with diagnosis/AIDS flows before any HTA use.

## 1990 option

The scripts successfully captured joint adult population tensors and childhood
stocks immediately before 1990 and 2025 transitions. Those states can inform
initialisation design. They are neither observed populations nor complete
restart checkpoints. Required evidence/assumptions remain unresolved in
`configs/initialisation_1990_readiness.json`; no 1990 restart was implemented.

## Evidence and reproduction

The production engine and scientific target definitions were unchanged. New
code is isolated in `historical_transmission_probe.py` and
`run_historical_transmission_probe.py` with guards in
`tests/test_historical_transmission_probe.py`.

- Raw experiment, frozen design, score report and state files:
  `outputs/calibration/historical_transmission_diagnostic_20260917/`.
- Independent hash verification and recomputed means/scores:
  `outputs/calibration/historical_transmission_review_20260917/VERIFIED_RESULTS.md`
  and `verification.json`.
- Full before/after suite: 20 new tests pass, unchanged 46 failures and 5 errors;
  2137 passes after, compared with 2117 before. One new metadata-config skip.
  Full identities/logs in `outputs/calibration/historical_transmission_prepared_20260917/`.

The independent review recomputed primary scores directly from raw replicates,
checked all experiment artifact hashes, and inspected the saved state summaries.

Recreate a review in a new empty directory (no simulations):

```bash
.venv/bin/python tools/review_historical_transmission_probe.py \
  --source outputs/calibration/historical_transmission_diagnostic_20260917 \
  --output-dir outputs/calibration/historical_transmission_review_repeat
```

The completed experiment is frozen. The runner intentionally refuses reused
scientific seeds or an occupied output directory. Repeating the scientific run
requires an explicit new design/seed decision; it is not the recommended next step.
