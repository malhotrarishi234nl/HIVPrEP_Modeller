# Temporal interpolation audit — 2026-09-21

> Archived under `documentation/reports/calibration/`; original filename retained.

## Decision

Keep the active transmission betas fixed. Do not replace the current linear
diagnosis or ART schedules merely to repair the 2024 MSM diagnosis deficit.

The paired audits show that calendar-time interpolation matters, but defensible
anchor-preserving alternatives do not resolve the temporal fit conflict. Abrupt
stepwise schedules can lift 2024 to the observed level only by severely damaging
the earlier diagnosis path and the 2022 AIDS fit.

## Tests completed

### Diagnosis schedule and post-2019 shape

Artifact:
`outputs/calibration/temporal_diagnosis_shape_audit_20260921/`

- 2 accepted candidates, 11 profiles, 5 fresh paired seeds: 110 trajectories.
- All existing anchors were held exactly fixed for the geometry profiles.
- Tested linear annual probability, linear hazard, linear logit, log-linear MSM
  multiplier, their restrained combination, and a stepwise boundary.
- Separately tested gradual and delayed post-2019 MSM ascertainment changes from
  -15% to +30%, with the calibrated path frozen through 2019.
- Log-linear interpolation of the MSM multiplier modestly improved historical
  diagnosis RMSE and primary I_max for both candidates, but left 2024 far below
  318 and did not improve the 2024/2019 temporal ratio.
- No post-2019 profile passed the predeclared strict gate. The strongest delayed
  +30% profile raised 2024 diagnoses only to 209 and 194, versus 318 observed,
  and slightly worsened the 2022 AIDS error.
- Stepwise diagnosis schedules raised 2024 diagnoses to 366 and 354 but produced
  grossly incorrect 2010/2019 diagnoses and 2022 AIDS.

### ART-initiation interpolation

Artifact:
`outputs/calibration/art_interpolation_shape_audit_20260921/`

- 2 accepted candidates, 7 profiles, 5 fresh paired seeds: 70 trajectories.
- Every existing ART-initiation and diagnosis anchor was held exactly fixed.
- Tested linear annual probability, linear hazard, smoothstep, stepwise, and
  combinations with the diagnosis audit's best log-linear MSM geometry.
- Smooth alternatives caused modest, candidate-dependent changes. No profile
  improved diagnosis RMSE, primary I_max, and 2022 AIDS error across both
  candidates.
- Stepwise ART initiation reached 322 and 318 diagnoses in 2024, but increased
  2010 diagnoses to about 894 and 904 and 2022 AIDS to 126 and 137. It is an
  informative boundary result, not a defensible model specification.

## Interpretation

The present problem is not simply that linear interpolation creates an artificial
plateau. Diagnosis counts are generated from the evolving undiagnosed pool, while
ART changes both treatment and future transmission. Smoothly changing how fixed
anchors are joined cannot create the observed post-2019 shape without moving the
underlying pools enough to disturb earlier diagnoses or AIDS.

The stepwise results demonstrate that the model can mechanically produce the 2024
level when treatment/diagnosis timing is changed drastically. Their simultaneous
failure at earlier years shows that adopting abrupt era changes would move error
rather than solve it.

Absolute five-seed means differ between the two audits because each deliberately
uses a fresh seed block and the simulator has material stochastic variance. All
decisions must therefore use paired within-audit comparisons, not compare one
audit's baseline mean directly with the other's.

## Consequence for the next model decision

The straightforward interpolation avenue is exhausted. Do not add increasingly
flexible splines or more knots solely because the tested profiles failed. The next
structural question should concern why the post-2019 MSM infection/undiagnosed pool
contracts too quickly, using independent evidence to distinguish:

1. time-varying risk/contact behaviour or population turnover;
2. diagnosis/ascertainment data mapping not represented by a single annual hazard;
3. treatment-state or disease-duration structure; and
4. a remaining population-denominator or entry-pathway mismatch.

Any next test should change one of these mechanisms explicitly and retain the same
2000–2015, 2015–2019, 2019–2024, 2022 AIDS, and overall I_max gates.
