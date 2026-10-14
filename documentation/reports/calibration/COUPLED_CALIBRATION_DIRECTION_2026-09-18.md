# Track 1 revision: search coupled histories before attributing failure to structure

> Archived under `documentation/reports/calibration/`; original filename retained.

## Decision following discussion with Rishi

Prioritise a joint, adaptive search of the existing model. Keep temporal
transmission flexibility as a separately specified comparator. Do not assume
that failure of fixed perturbations demonstrates missing structure. No simulator,
target, bounds, scoring or acceptance changes, and no runs, were made here.

## Verified facts

- The simulator propagates its full population state through quarterly steps;
  progression, diagnosis, ART and mortality act on existing stocks. Compounding
  and parameter interactions are already present in each simulation.
- The focused run's manifest records 622 evaluated vectors in wave 1, seven
  diagnosis targets and two primary survivors. The sequence stopped at the
  declared minimum of ten. The planned joint wave was not reached.
- This is not literally one-parameter-at-a-time calibration: the LHS varies the
  33 parameters jointly. However, it does not adapt proposal directions to the
  compensating parameter combinations indicated by residuals.
- Later-wave proposal code uses retained coordinate minima/maxima and nearest-
  neighbour thinning. A sampled box contains the retained points, not necessarily
  the full unknown non-implausible region. This heuristic can omit unsampled
  regions. It did not cause the focused wave-1 stop because wave 2 never ran.
- The fixed-profile diagnostic varied transmission while holding other parameter
  values fixed. Its negative results are conditional on that design.

## Revised specification requirements

1. Keep the existing simulator, initial year and 15-target observation contract
   as the first comparator. Keep acceptance distinct from candidate generation.
2. Represent effective transmission and diagnosis combinations explicitly. Remove
   a parameter only when equivalence is established, or retain its uncertainty
   through profiling/sensitivity. Do not collapse activity heterogeneity merely
   because contact scales multiply; heterogeneity affects epidemic dynamics and
   PrEP targeting. Preserve the original feasible domain when reparameterising.
3. Build a deterministic residual vector (given frozen CRN seeds) from all 15
   anchors for every evaluated candidate. Organise reporting by historical era
   and mechanism, but use one continuous simulated history. Keep signed residuals
   to reveal early deficits coupled to later excesses.
4. Use multi-start adaptive proposals that change multiple blocks together:
   initial burden/composition, effective transmission and mixing, diagnosis,
   progression/ART/survival. Include cross-block moves, several trade-off starting
   points and continued exploration of the original box. Neither a single best
   point nor the coordinate range of two survivors should become the only search
   domain. Near misses may guide search without becoming accepted members.
5. A search objective can guide exploration without replacing the existing final
   maximum-implausibility rule. Specify its scaling before execution; report
   block residuals and process variance so high-variance poor means cannot be
   quietly presented as a good fit. Confirm promising candidates on fresh seeds.
6. Era-wise work is cumulative: revisit shared/early parameters when later data
   reveal a conflict. Do not fit and freeze each era independently, reset states
   at era boundaries, or treat modelled PLHIV as observed truth before fitting
   progression. A chronological warm start is a proposal device, not a final fit.
7. Classify restrictions before changing any: mathematical validity (probability,
   simplex, contact balance); substantive assumptions (base diagnosis monotonicity,
   seed composition ordering, orientation bounds); computational heuristics
   (proposal boxes, thinning, minimum survivor count). Improving exploration need
   not relax the scientific acceptance rule. Scientific bounds/uncertainty changes
   require their own justification, not a need to obtain survivors.
8. Freeze a finite evaluation budget, seed allocation and termination criteria
   before launch. Output complete residual trajectories, initial/policy-year
   state distributions and exact acceptance results. Failure means no adequate
   candidate found within this search, not a global impossibility theorem.

## What this changes from 17 September

Temporal flexibility is not discarded. It moves behind an adequately attempted
coupled fit of the existing model. The next executable work should implement
candidate generation and full-history residual reporting for that joint search,
with tests that it can locate a narrow compensating region and that proposal
ranks cannot silently alter the acceptance contract. Do not launch more isolated
mechanism probes to re-establish familiar causal relationships.

No completed joint optimiser or new launch command is claimed by this document.
A numeric budget and full parameter/block mapping still belong in the executable
specification before launch.

## Evidence inspected

- `outputs/calibration/hm_focused_diagnoses_then_aids_20260916/manifest.json`.
- `run_history_matching.py`: module caveat, `restrict_space_to_nroy`,
  `thin_by_proximity`, `run_waves` loop and stopping logic.
- `run_lhs_calibration.py`: `_valid_draw_mask`.
- `run_v3.py`: `step_simulation` and `_step_contacts_and_mixing`.
- `documentation/reports/calibration/HISTORICAL_TRANSMISSION_RESULT_2026-09-17.md`.

Methodological context: Andrianakis et al. (2015), PLoS Computational Biology,
https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1003968;
Andrianakis et al. (2017), efficient sampling of small non-implausible regions,
https://epubs.siam.org/doi/10.1137/16M1093008.
These support attention to joint-region exploration, not proof that a solution
exists for this model or endorsement of a particular unimplemented optimiser.
