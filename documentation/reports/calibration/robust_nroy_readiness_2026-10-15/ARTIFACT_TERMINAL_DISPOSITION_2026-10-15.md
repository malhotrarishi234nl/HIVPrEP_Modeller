# Terminal disposition of the final nine output artifacts

## Decision rule

The owner selected Option B: a new V13.22-on-1980 calibration. None of the nine
remaining artifacts was produced from the resulting fitted Option B model,
because that calibration has not yet been run. The conservative and reversible
handoff disposition is therefore to retain every artifact in place as
`HISTORICAL_RETAINED` and exclude it from current scientific claims.

This is not a finding that the underlying data, assumptions, or historical
analyses are wrong. A later current-code review may re-adopt an input, and a
new Option B run may reproduce an analysis. Until that happens, citation or
path presence is not current scientific acceptance. No file is deleted, moved,
rewritten, or resealed by this disposition.

## Retired diagnosis and conference chain

- `outputs/calibration/diagnosis_triangulation_orchestration_20260922`
- `outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922`
- `outputs/calibration/followup_knot_and_late_dx_20260929`

These remain useful development evidence, but they predate the selected Option
B calibration and cannot be the fitted production claim source for that model.

## Retired evidence and assumption packages

- `outputs/calibration/rivm_aids_vintage_audit_20260916`
- `outputs/hta_results/ae_adherence_corridor_20260914_225729`
- `outputs/hta_results/aids_at_diagnosis_20260914_183434`
- `outputs/hta_results/b1_b2_calibration_space_20260914`
- `outputs/hta_results/rivm_prep_targets_20260914_134653`

Their contents may inform a new review, but that review must explicitly bind
the adopted input or assumption to the new signed analysis contract. They are
not silently carried forward.

## Retired variance package

- `outputs/hta_results/a5_ensemble_variance_20260914_150556`

The existing variance estimate is retained as historical evidence. Any process
variance used with the newly calibrated Option B model must be re-estimated or
independently justified and frozen under the new acceptance-input contract.

## Consequence

The artifact catalog can now give every in-scope top-level output directory a
terminal preservation status. It still contains no `CURRENT_VALIDATED` result,
which is correct until the new calibration and downstream scientific gates are
completed.
