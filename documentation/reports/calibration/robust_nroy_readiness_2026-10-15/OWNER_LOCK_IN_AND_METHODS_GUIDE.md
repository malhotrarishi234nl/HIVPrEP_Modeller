# Owner lock-in and methods guide — 2026-10-15

## Status of this note

This note records the thesis owner's current direction and explains the main
technical assumptions in plain language. It is not, by itself, a signed
production plan or an authorization to run the scientific engine.

All scientific, methods, provenance, compute, and final-approval decisions are
owned by the thesis owner, Rishi Malhotra. Named reviewer roles in the decision
register describe the kind of review required; they do not imply that another
person owns the work.

## Design principle

Use the simplest design that states the scientific claim honestly and fails
loudly when its evidence is missing. Do not add machinery merely to make the
workflow look sophisticated. Conversely, do not use placeholders, silent
fallbacks, or undocumented deferrals that can become larger problems later.

A deferred limitation must have:

1. an explicit statement of what is unknown;
2. the direction in which it may bias the result;
3. a trigger for revisiting it; and
4. a new version/hash if revisiting it changes the acceptance contract.

## One canonical config for M0 and M1

The preferred design is one ordinary, stochastic model configuration that can
run independently of calibration and contains the optional
`stage_structured_diagnosis` block.

- Ordinary standalone M0: `enabled = false`.
- M1: `enabled = true`, with the complete M0 parameter vector plus the added
  `aids_symptom_annual_probability` coordinate.
- M1 identity boundary: `enabled = true`,
  `routine_hazard_scale = 1.0`, and
  `aids_symptom_annual_probability = 0.0`.
- Calibration is an optional parameter-selection/injection wrapper. The model
  must continue to accept an ordinary config and run without any calibration
  machinery.

The production adapter should set the M0/M1 switch explicitly on a copied
runtime config. It must not infer the model from an incidental value left in
the base file. A genuine M0 runtime copy may remove the optional block to
preserve the exact legacy route; an M1 runtime copy must explicitly enable it.

The canonical file should default to M0 (`enabled = false`) so that a normal
standalone run cannot accidentally activate the experimental M1 mechanism.

### What `routine_hazard_scale = 1.0` means

It is a multiplier on the existing routine diagnosis **hazard**, not a 100%
diagnosis probability.

- `1.0`: leave the pre-existing routine diagnosis process unchanged.
- `0.8`: multiply that routine hazard by 0.8.
- `0.0`: remove the routine diagnosis hazard.

M1 adds a separate AIDS-symptom diagnosis hazard. Pinning the routine scale to
`1.0` ensures that M1 changes only the new symptom mechanism. At exact `p = 0`,
the engine dispatches to the legacy diagnosis route, making M1-at-zero nest M0
rather than combining a new mechanism with a changed baseline.

## Observation uncertainty and `V_obs = 0`

For each target the implausibility denominator contains a base variance:

```text
B_i = V_obs + V_code + V_discrepancy + declared extra non-process terms
```

Candidate process variance is added separately during scoring. If the true
observation uncertainty is positive but `V_obs` is set to zero, the denominator
is too small, the implausibility score is too large, and the method rejects
more parameter vectors. This is **over-exclusion**: the reported NROY set is a
subset of what it might have been with defensible observation uncertainty.

Approved wording:

> Where no defensible observation-error variance can be derived from the
> available source, the primary analysis assigns `V_obs = 0` as a conservative
> screening assumption. This is not a claim that the observation is measured
> without error. It biases toward over-exclusion and is therefore reported as
> a limitation.

This may be used as the current model assumption. A later non-zero value must
be introduced as a new, versioned acceptance contract and reported as a
sensitivity analysis, not silently substituted into the original results.

## Methods decisions: established principles versus engineering choices

The literature supports iterative history matching, maximin/space-filling
design, stochastic replication, correlated and potentially disconnected NROY
geometry, and cautious sampled connectivity claims. It does not prescribe the
project's exact scales, quotas, separation values, or graph constants.

The current simplest defensible starting choices are:

- normalized Euclidean radius scales `0.025` and `0.05`;
- 16 maximin ambient probes per scale from normalized Gaussian directions;
- maximum 2,000 redraw attempts, with invalid constrained proposals reported
  rather than clipped;
- numerical near-duplicate tolerances of `1e-4` for anchor-neighbour and
  neighbour-neighbour separation;
- two confirming neighbours and two independent direction classes;
- a zero-cost label sensitivity using one direction class;
- design-only tolerance sensitivity at `1e-6`, `1e-4`, and `1e-3`;
- bridge fractions `0.25`, `0.5`, and `0.75`, with every declared bridge probe
  required to confirm;
- maximin selection of two representatives per supported component;
- `alpha = 0.05` for a one-sided 95% upper process-variance bound used only to
  allocate confirmation compute; final acceptance remains nominal on 20 fresh
  seeds.

These numbers must be described as frozen engineering choices with sensitivity
checks, not as universal constants taken from the literature.

## Newly identified design/code gaps

These must be closed before the methods choices above are signed.

### 1. Declared directed-probe scales are not guaranteed coverage

The current directed-probe implementation visits all directions at the first
scale and stops when a total quota is filled. A quota of four can therefore
exercise only `0.025` while the plan declares both `0.025` and `0.05`.

Required correction: freeze the direction basis, construct the complete
`direction x scale x sign` design, and derive the trajectory count from it.
An incompatible partial quota must fail rather than truncate a declared scale.

### 2. `rank_truncation` is not numerical rank truncation

The current field retains the first N direction vectors. It does not perform
SVD, QR, independence filtering, or condition-number analysis.

Required correction: rename the concept to `max_directions` and retain the
existing explicit collinearity/support checks. Do not add rank-revealing linear
algebra unless a real analysis later requires it.

### 3. `k = 3` is not a literature standard — corrected in code

A fixed k-nearest proposal graph can create apparent components simply because
an inter-component edge was never proposed.

Implemented simplification: compute all pairwise normalized distances over the
confirmed anchors, build a deterministic Kruskal minimum spanning tree, and
test its `n - 1` edges. The unsigned graph-neighbour-count decision was removed
because it is no longer load-bearing. A failed sampled edge is unresolved, not
proof of disconnection. Only search bounded alternate paths when the unresolved
edge would change representative allocation.

The permitted claim is "sampled straight-path support under the declared
bridge design," not continuous connectivity.

### 4. The variance-bound formula and label disagreed — corrected in code

The prior code used `chi2.ppf(alpha / 2)`, which at `alpha = 0.05` was the
upper endpoint of a two-sided 95% interval, equivalently a 97.5% one-sided
upper bound. The documentation calls it a one-sided `1 - alpha` bound.

The promotion calculation now uses the direct one-sided 95% formula

```text
sigma_upper^2 = (K - 1) * s^2 / chi2.ppf(alpha, K - 1)
```

and keeps it exclusively as a promotion/compute-allocation safeguard. Final
nominal acceptance is unchanged. The pilot must report how many candidates are
promoted only by this bound.

## Reading guide

Read these in this order; the first three are closest to this thesis model.

1. Andrianakis et al., Bayesian history matching of infectious-disease models:
   <https://doi.org/10.1371/journal.pcbi.1003968>
2. Andrianakis et al., HIV history matching with variance emulation:
   <https://doi.org/10.1111/rssc.12198>
3. Andrianakis et al., efficient history matching of a high-dimensional
   individual-based HIV model: <https://doi.org/10.1137/16M1093008>
4. Morris and Mitchell, maximin exploratory designs:
   <https://doi.org/10.1016/0378-3758(94)00035-T>
5. Ranjan, Bingham, and Michailidis, sequential contour design:
   <https://doi.org/10.1198/004017008000000541>
6. Binois et al., replication versus exploration for stochastic simulation:
   <https://doi.org/10.1080/00401706.2018.1469433>
7. NIST, one- and two-sided chi-square variance intervals:
   <https://www.itl.nist.gov/div898/handbook/eda/section3/eda358.htm>
8. Kruskal, minimum spanning trees:
   <https://doi.org/10.1090/S0002-9939-1956-0078686-7>
9. Kavraki et al., sampled roadmaps and local path checks:
   <https://doi.org/10.1109/70.508439>

## Owner directions recorded in this session

- One thesis owner holds all decision roles.
- Proceed toward one canonical optional-M1 config, built and reviewed carefully.
- Retain the current M0 calibration-space proposal subject to bound review.
- Permit `V_obs = 0` where underivable as an explicit conservative model
  assumption that can be refined in a later version.
- Use the simplest literature-consistent design for probe, support, graph, and
  promotion settings, with small predeclared sensitivities.
- Retain two maximin representatives per supported component.
- Complete the seed audit before allocating seeds.
- Use a measured pilot before pricing or signing compute caps.
- Clean and organize the repository and artifacts so it can be handed to
  another researcher, while preserving provenance and avoiding bulk deletion.
- Proceed with the identity and positive-p M1 evidence sequence after the
  preceding gates are complete and separately authorized.

