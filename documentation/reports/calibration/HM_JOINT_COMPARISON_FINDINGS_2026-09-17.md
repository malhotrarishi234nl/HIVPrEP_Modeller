# Completed controlled comparison: findings and next decision

> Archived under `documentation/reports/calibration/`; original filename retained.

## Status

90/90 simulations completed, zero errors, eight workers, 108.3 seconds elapsed.
Nine configurations each used ten seeds, 195000–195009. All used stochastic mode
and the enabled MSM diagnosis path. This is a local diagnostic experiment, not a
calibration wave, posterior, or NROY update. Raw files remain unchanged at
`outputs/calibration/hm_joint_fit_comparison_20260917/`.

## Main result

Earlier diagnosis improves early AIDS recognition and modestly reduces later
diagnosis excess. Larger initial prevalence and lower AIDS mortality increase
early AIDS but worsen later counts. No tested cell meets R1a, so none satisfies
the full prespecified focused-search trigger. Neither F1-only nor F2-only meets
the R2 1.5-fold recognition threshold. R3 applies to these tested changes only.

| Cell / change | MSM AIDS 1990 | MSM diagnoses 2000 | MSM diagnoses 2004 |
|---|---:|---:|---:|
| Observed | 318 | 360 | 572 |
| A_000: baseline 620 | 84.6 | 524.8 | 800.6 |
| A_100: earlier diagnosis | 105.7 | 495.8 | 764.1 |
| A_010: lower AIDS mortality | 105.0 | 782.7 | 1278.0 |
| A_001: larger initial prevalence | 136.6 | 860.4 | 1311.6 |
| A_111: all changes | 219.4 | 1218.8 | 1983.6 |
| B_000: measurement anchor 548 | 355.8 | 1679.5 | 744.7 |

Numbers are arithmetic means over ten seeds. Earlier diagnosis produces a
paired geometric recognition ratio of 1.250 (exploratory 95% t interval
1.207–1.295) and diagnosis-2000 ratio 0.944 (0.932–0.957). These are controlled
local contrasts, not global causal bounds or multiplicity-adjusted discoveries.

Primary joint Imax improves from 6.733 at baseline to 5.882 for earlier diagnosis.
No cell passes cutoff 3 under primary discrepancy 0.1 or sensitivity 0.2.
The latter Imax values are 3.983 and 3.483, respectively, binding on AIDS 2022.
Thus repairing only early AIDS would not establish joint compatibility.

The earlier-diagnosis cell has no factor-of-two window exits relative to baseline.
Cells whose early AIDS reaches 159 fail the diagnosis-2000/2004 windows. The
interaction contrasts examined for AIDS 1990 and diagnoses 2000/2004 give no clear
evidence of an interaction overcoming this trade-off at these factor levels.
This is not proof of absence of interactions elsewhere.

## Accounting and validation limitations

- Source artifact hashes verify; each cell has ten unique specified seeds.
- F1-only pre-1985 saved outputs match baseline exactly seed-for-seed.
- All-orientation undiagnosed-AIDS balance for 1985–1990 leaves mean residuals
  684.5 at A and 1069.3 at B. Label these **all other net flows**, not missed AIDS
  diagnoses. They do not separate deaths, ageing out and migration, or provide
  a cohort-specific estimate of the MSM 1990 observation gap.
- A stock balance residual calculated this way is not an independent verification
  of its component routes. Do not call the residual explained without measuring
  or reconciling those routes.
- Ten replicates support a local experiment, not high-precision confirmation.
- The known deterministic multiplier-one allocation test failure remains a
  separate recorded limitation; this experiment never switches allocation paths.

## What is closed, and what remains open

Closed for this run: the specified 25%-of-range changes in early diagnosis,
initial prevalence and mortality around 620 did not meet the declared search
trigger. More seeds on these same cells or a repeat of this experiment is not
the next priority. Do not widen discrepancy or demote targets to obtain survivors.

Open: whether the early AIDS observation corresponds adequately to the model's
recognition process; whether other in-box regions can fit; and whether historical
transmission inputs need change. None of those explanations is established.

## Next bounded step — one observation-contract decision, no new wave

Before proposing an AIDS-presentation feature, answer precisely:

**What event does the RIVM early MSM AIDS series count, and does the existing
model counter represent that event with the same timing and eligibility?**

Deliver one short decision memo using the existing source corpus and code:

1. Trace the early RIVM table to its surveillance definition: clinical AIDS onset,
   first AIDS diagnosis, report year or another date; HIV already diagnosed versus
   concurrent diagnosis; route eligibility; retrospective registration and deaths.
   Quote/page-reference only what the source establishes; list what is unknown.
2. Map those definitions to progression while diagnosed and HIV diagnosis in AIDS.
   Distinguish a counter/mapping change from an AIDS-specific diagnosis hazard:
   the latter changes care entry, subsequent dynamics and economics, not merely
   reporting. Do not fit a presentation effect just to close this residual.
3. If a source-backed mismatch is established, propose the smallest defensible
   mapping or mechanism correction, its evidence/assumptions, and synthetic tests;
   request approval before implementing it.
4. If the current mapping is appropriate, or sources cannot resolve the question,
   explicitly close the audit as such. Do not open another broad literature review
   or treat uncertainty as proof a new mechanism is needed. Present the remaining
   choice: a separately bounded within-model search versus an evidence-based
   structural proposal. These are user decisions, not automatic continuations.

Stopping condition: a written **mapping appropriate / mismatch established /
definition unresolved** verdict, with one recommended action and its justification.
No production run, new model feature, or additional parameter sweep is part of
this next step. This prevents repeatedly trying general parameters to compensate
for an observation question that has never been settled.

## Reproduction

From repository root: `.venv/bin/python tools/summarize_joint_fit_comparison_20260917.py`.
The script verifies saved artifact hashes, seed/mode contracts, recomputes target
means and predictive-local scores, paired log-ratio intervals, R1a, window exits,
pre-1985 identity and the accounting residual. It prints JSON without modifying
files or launching simulations. Targets are loaded from the current focused
register; preserve source provenance when reusing after any register changes.
