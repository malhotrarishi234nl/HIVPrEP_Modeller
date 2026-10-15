# History matching: concepts, operation, and interpretation

**Updated:** 2026-09-15  
**Status:** **HISTORICAL SNAPSHOT** of the September 15 learning-wave workflow;
two post-fix stochastic waves completed and stochastic confirmation was
prepared but not run.

This is the practical guide to the repository's 2026-09-15 calibration method.
It is retained to interpret that workflow and its artifacts; it is not the
current Option B/robust-NROY production contract. Current status, target and
discrepancy assumptions, and launch gates are in
`../reports/calibration/robust_nroy_readiness_2026-10-15/README.md`. The
methodological build history and literature rationale remain in
`HISTORY_MATCHING_BUILD_SPEC.md`. Run-specific declarations remain in the
dated `HM_*_RUN_*.md` files.

## 1. What history matching does

History matching is parameter-space exclusion, not posterior estimation.

1. Draw complete parameter vectors from the declared calibration space.
2. Simulate each vector.
3. Compare its simulated outputs with every declared calibration target.
4. Rule out a vector if it is too implausible for any target.
5. Focus the next wave on the region occupied by the vectors not ruled out yet.

The surviving set is called **NROY**: Not Ruled Out Yet. Its members have no
probability weights. A member is not proved correct, and one NROY member is not
more probable than another merely because its implausibility is smaller.

History matching therefore supports statements such as “these parameter
vectors were not ruled out under the declared uncertainty budget.” It does not
support “this is the posterior,” “this is a credible interval,” or a probability
that one epidemiological vector is correct.

## 2. Parameter-vector IDs are not random seeds

Every row of a wave design is one complete vector containing all 32 calibrated
parameters. `design_id` is the row identifier within that wave.

For example, Wave 2 design IDs `25` and `342` identify two different complete
32-parameter vectors in:

`outputs/calibration/hm_focused_continuation_20260915/wave_02/evaluated.csv`

They are not seeds and are not globally meaningful outside that source wave.
The original Wave 2 evaluation simulated every vector with the same three CRN
seeds: 91000, 91001, and 91002. Using the same seeds across parameter vectors
reduces irrelevant between-vector Monte Carlo noise while preserving a
stochastic estimate of each vector's expected output.

## 3. Implausibility and the acceptance rule

For target `i` and parameter vector `x`:

```text
I_i(x) = | E[f_i(x)] - z_i |
         / sqrt(V_obs,i + V_code,i + V_ensemble,i + V_disc,i)
```

- `E[f_i(x)]`: replicate-mean simulator output.
- `z_i`: observed target.
- `V_obs`: observation uncertainty.
- `V_code`: emulator uncertainty; zero because this implementation evaluates
  the simulator directly.
- `V_ensemble`: stochastic simulator variance of the replicate mean.
- `V_disc`: declared allowance for model-versus-reality discrepancy.

The primary combination rule is:

```text
I_max(x) = max_i I_i(x)
NROY      = {x : I_max(x) <= 3}
```

This is non-compensatory. A good match to 18 targets cannot cancel a bad match
to the nineteenth. `I_(2)` and `I_(3)` are reported as diagnostics only.

The variance budget standardizes each difference; it is not a probability
weight. Consequently, an output that appears numerically distant from its
target may remain NROY if the prespecified uncertainty budget is large. That is
why the component variance table must always accompany an NROY count.

## 4. Historical September estimator and target contract

The September learning waves used:

- stochastic simulation, not deterministic calibration;
- three replicate seeds per design point for the search stage;
- the same replicate seeds at every design point (CRN);
- A-5's independently measured per-run `V_ensemble`, divided by the number of
  replicates averaged;
- 19 prespecified Tier-1 trajectory anchors:
  - ECDC HIV confirmed cases, 1990–1995;
  - SHM MSM diagnoses, seven anchors from 1996–2024;
  - ECDC AIDS confirmed cases, six anchors from 1990–2017;
- Tier-2 UNAIDS/Spectrum series as cross-validation only;
- non-MSM diagnoses excluded from calibration because C-1 injects those
  diagnoses as an observed exogenous input.

The primary learning rule uses `V_disc = 0.10 × Var(outputs across the wave)`.
This follows the documented illustrative Andrianakis rule but is not yet the
final scientific discrepancy choice. Target-relative 10% and 20% rules are
reported as sensitivities and cannot rescue or continue the primary sequence.

## 5. Wave execution and focused continuation

`run_history_matching.py` runs a declared wave plan. A complete run writes a
manifest containing input hashes, runtime-module hashes, run settings, and
hashes for every artifact.

The first wave samples the full calibration box. A later wave can be run in the
same invocation or started from a completed run:

```bash
./.venv/bin/python ./run_history_matching.py \
  --wave-plan PATH_TO_NEXT_PLAN.json \
  --base-config PATH_TO_BASE_CONFIG.json \
  --calib-space PATH_TO_CALIBRATION_SPACE.json \
  --continue-from PATH_TO_COMPLETED_PARENT_RUN \
  --output-dir PATH_TO_NEW_OUTPUT_DIRECTORY \
  --workers 8 \
  --acknowledge-prerequisites
```

Continuation is not a loose import of “good rows.” Before simulation, the
runner verifies:

- every parent artifact against its recorded size and SHA-256 hash;
- an error-free final parent wave;
- a non-empty NROY set above the declared minimum size;
- the NROY row count, parameter bounds, and `I_max` cutoff;
- identical base configuration, calibration space, target register, and
  `V_ensemble` source;
- compatible scientific and scoring runtime modules;
- that the new plan begins at exactly the next wave index.

The parent remains read-only. The continuation writes a new directory and a
provenance link to its parent. Future Wave 3, Wave 4, and later runs can be
chained in the same way.

Without an emulator, the exact continuous NROY boundary is unknown. The next
design therefore samples the axis-aligned bounding box of the prior NROY
members and removes proposals that are too far from their observed point cloud.
This is a conservative superset approximation: some new points will fall
outside true NROY and be ruled out after simulation.

## 6. Confirmation runs

A small NROY set found from only three replicates can contain “lucky” vectors:
their three-seed mean may be closer to the observations than their long-run
expected output. This is selection noise, not a parameter-space result.

`run_hm_confirmation.py` checks selected vectors without treating the check as
a new wave. It:

- checksum-verifies the source run;
- rejects IDs not contained in the named source sensitivity set;
- extracts the exact complete parameter vectors;
- runs many fresh seeds for each vector, parallelized seed by seed;
- exports seed-level outputs, confirmed means, sample variances, and empirical
  standard errors;
- rescales the independent A-5 `V_ensemble` by the confirmation replicate count;
- recomputes target-relative 10% and 20% `I_max`;
- reports the standardized shift from the original three-seed mean.

Confirmation does not update NROY geometry and does not turn confirmed vectors
into posterior draws. The current prepared confirmation is documented in
`HM_STOCHASTIC_CONFIRMATION_RUN_2026-09-15.md`.

## 7. What counts as success

No single acceptance percentage establishes calibration success. Evaluate the
following together:

| Dimension | Evidence of success | Warning sign |
|---|---|---|
| Execution | no errors; complete checksummed exports | missing outputs or failed simulations |
| Contraction | NROY volume falls across focused waves | no contraction or arbitrary bound changes |
| Adequacy | `I_max <= 3` under a prespecified defensible budget | survival only under a very large `V_disc` |
| Stability | confirmed many-seed means remain NROY | three-seed survivor fails with more seeds |
| Robustness | conclusions persist across justified discrepancy readings | NROY changes completely with the discrepancy convention |
| Validation | held-out series show acceptable trajectories | systematic held-out mismatch |
| Ensemble utility | enough joint parameter vectors remain to represent structural scenarios | one or two isolated vectors treated as a distribution |

The objective is not to maximize the number accepted. Too many survivors can
mean the uncertainty budget is uninformative; too few can mean sparse sampling,
conflicting targets, overly tight uncertainty, or structural model mismatch.
Those explanations must be distinguished rather than repaired by moving the
cutoff after observing the result.

## 8. Results through Wave 2

### Wave 1 — broad learning wave

- 1,000 parameter vectors; three replicates each; zero errors.
- 46 primary NROY members (4.6%).
- Primary NROY bounding volume: 7.05% of the original parameter box.
- One 10% target-relative survivor.
- Primary variance budgets dominated by `V_disc` for all 19 targets.

### Wave 2 — focused continuation

- 1,000 proposed; 974 after proximity thinning; three replicates each; zero errors.
- 90 primary NROY members within the focused design (9.24% local acceptance).
- Primary NROY bounding volume: 1.42% of the original parameter box—about an
  80% reduction from Wave 1's bounding volume.
- Two survivors under both the 10% and 20% target-relative sensitivities:
  design IDs `25` and `342`.
- Design 25 has sensitivity `I_max = 1.4205`; design 342 is borderline at
  `I_max = 2.9564`.
- The primary budget remains `V_disc`-dominated for all 19 targets.
- `V_ensemble` dominates 18 of 19 targets under the 10% target-relative rule.
- Female PLHIV remains systematically underpredicted in Tier-2 cross-validation.

Interpretation: the search and continuation mechanisms work and have located a
small promising region. The broad primary set is not a final calibrated
ensemble because its acceptance is driven by the output-variance discrepancy
allowance. The two stricter survivors require many-seed confirmation before
they can guide another focused wave or downstream economic scenarios.

## 9. Output-reading order

For any completed wave, read:

1. `RUN_SUMMARY.md` and `manifest.json` for completion and provenance.
2. `wave_XX/wave_summary.json` for counts and stopping reason.
3. `wave_XX/variance_budget.csv` before interpreting NROY.
4. `wave_XX/binding_targets.csv` for the targets doing the excluding.
5. `wave_XX/per_target_implausibility.csv` for target-level distributions.
6. `wave_XX/parameter_ranges.csv` for marginal contraction.
7. `wave_XX/nroy_members.csv` for complete joint parameter rows.
8. `wave_XX/cross_validation/` for held-out behavior.
9. `wave_XX/sensitivity_*/` for discrepancy robustness.

Never construct an ensemble by sampling independently from the marginal ranges
in `parameter_ranges.csv`; that destroys the joint parameter dependence. Use
complete rows from `nroy_members.csv`.
