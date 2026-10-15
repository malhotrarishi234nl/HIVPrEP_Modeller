# Positive-p M1 anchor confirmation plan

This is an execution specification only. No engine work was run.

## Candidate source

Candidates must come from an explicitly documented M1 discovery/search whose
rows contain all 34 coordinates in the signed M1 order. Candidate selection must
be frozen before the reserved confirmation outcomes are inspected. Acceptable
sources are a new current-code M1 search or a prospectively declared current-code
screen; historical M0-only anchors and marginal recombinations are not positive-p
M1 candidates.

Each candidate transport CSV must contain a unique `candidate_id`, every M0
coordinate, and `aids_symptom_annual_probability > 0`. The vector hash is
computed over the complete ordered joint vector. Missing coordinates, duplicate
vectors, p=0 rows, or marginal recombination fail closed.

## Confirmation design

- Use exactly the signed, reserved 20-seed `seeds.confirmation` block.
- The block must be disjoint from discovery, candidate generation, pilots,
  prior experiments, and every seed source enumerated by the seed audit.
- Evaluate every candidate on all 20 seeds with the signed stochastic
  M1-capable config, exact target order, current scientific code/runtime, and
  the frozen observed/B_i evidence.
- Record candidate-level `n_replicates = 20`, nominal `I_max`, and Boolean
  `nominal_pass = (I_max <= 3)`. Sensitivity-only or upper-variance-
  bound passes are not confirmations. The validator recomputes nominal `I_max`
  from the seed records; these aggregate fields are cross-checks and cannot
  override the recomputed verdict.
- Preserve a typed seed-result artifact for each candidate using
  `robust_nroy_m1_anchor_seed_results/1.0.0`. It must bind the complete ordered
  M1 vector, frozen target order, exact acceptance-input identity, and one
  unique record for every reserved seed. Every record repeats the candidate
  and vector hash and contains a complete finite target-output vector.
  Aggregate-only scores are insufficient.
- Reference each seed-result artifact by a package-local relative path plus its
  exact SHA-256. Absolute paths and paths that escape the evidence package fail
  closed. See `templates/m1_anchor_seed_results.json.template`.

The final evidence must validate against
`robust_nroy_m1_anchor_confirmation/2.0.0`; the template is
`templates/m1_anchor_confirmation.json.template` and the existing
`_validate_m1_anchor_evidence` routine verifies plan/config/code/runtime, target
order, seeds, complete vectors and outputs, acceptance basis, recomputed scores,
and pass flags.

## Discovery union after confirmation

M1 discovery is the deterministic union of:

1. every nominally confirmed M0 vector embedded at exact p=0; and
2. independently confirmed strictly positive-p M1 vectors from this run.

Candidate-ID collisions are legal only for identical vector hashes. Duplicate
vectors do not inflate quotas. The union's source counts, IDs, p values and
hashes must be sealed into the prepared M1 stage.

## Stop point

Before dispatch, require the signed plan, seed audit, identity gate, measured
pilot, priced/approved budget, selected config registry, and frozen acceptance
inputs. Request separate explicit authorization for the confirmation engine run.
