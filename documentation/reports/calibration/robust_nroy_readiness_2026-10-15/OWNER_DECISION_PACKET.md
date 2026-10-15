# Robust-NROY owner decision packet — 42 unsigned rows

**Status: CURRENT decision support as of 2026-10-15.** This packet is bound to
plan SHA-256
`76798841923717b78f7cf2f8f2185b5f5352fa30d545be9339f260f9c0b89e3c` and
decision-register SHA-256
`d6e03ac9d8b6e1d1b5dba6e23315368a67720ceaa2e199d714165878bf318270`.
It recommends decisions; it does not edit or sign the plan and does not
authorize an engine run.

The 42 rows are partitioned exactly once: **A 25 + B 8 + C 3 + D 3 + E 3 =
42**. The literature supports the method families, not this project's exact
numeric constants. Values below are deliberately simple engineering choices
for the current 33-dimensional M0 / 34-dimensional M1 contract and must be
reported that way. If the backbone decision changes that dimensionality or the
four-block partition, reopen every dimension- or block-dependent Group A row
before signing it.

## Recommended decision order

1. Review Group A now. These are method and code-contract choices that can be
   decided without pretending the model backbone is settled.
2. Resolve Group B using
   [`PRODUCTION_BACKBONE_RECONCILIATION.md`](PRODUCTION_BACKBONE_RECONCILIATION.md).
   If the production claim requires the later V13.22 diagnosis and two-era
   on-ART mechanisms, its recommendation is Option B: establish and recalibrate
   a scientifically justified 1980 base. Do not convert the 1975 diagnostic
   derivative into a nominal 1980 config by editing a few fields.
3. Review Group E against the source workbook and regenerate the acceptance
   inputs from the eventual signed plan.
4. Complete the Group D seed audit and reserve seeds before outcomes are seen.
5. Authorize a measured pilot; only its evidence may set Group C.

## Cost coupling before any number is signed

The recommended Group A local design contains, per anchor:

```text
anchor control                                      1
ambient: 16 probes x 2 scales                      32
archive-following: 2 sources x 1 direction
                   x 2 scales x 2 signs             8
cross-block: 6 block pairs x 2 scales x 2 signs    24
                                                   --
M0 local proposals                                 65
M1 p-boundary bin probes                             6
M1 local proposals                                 71
```

With five discovery seeds, this is at most **325 trajectories per M0 anchor**
and **355 per M1 anchor** before exact vector/seed deduplication. Confirmation
cost is `20 x number_promoted`. Three bridge fractions on a Kruskal tree cost
`3 x (N - 1) x 20 = 60 x (N - 1)` trajectories for `N` confirmed endpoints.
The separate positive-p M1 anchor confirmation costs `20 x candidate_count`.

Those are design identities, not a priced budget. The runner applies the
signed trajectory and attempt caps to each prepared ledger. Total workflow
cost also depends on how many anchors and promoted vectors exist, failures,
retries, worker count, machine throughput, and downstream representative use.
Do not fill Group C from these formulae alone.

## A. Method/code proposals supportable now — 25 rows

Recommended owner action: approve these only if the finite-design claim and
cost coupling above are acceptable. Code acceptance shows the contract is
enforced; it does not make the choices scientific facts.

| Row | Plan path | Simplest defensible recommendation | Bound or caveat |
|---:|---|---|---|
| 12 | `representative_selection.rule` | `maximin_geometry` | Uses parameter/baseline geometry only; policy outcomes remain unavailable. |
| 13 | `ambient.scales` | `[0.025, 0.05]` | Normalised radii; report that only these two local scales were tested. |
| 14 | `ambient.scale_semantics` | `radius` | One joint Euclidean radius, not a per-coordinate bound. |
| 15 | `ambient.quota_per_scale` | `16` | Primary total is 32 probes/anchor. Pre-run design sensitivity: 8 and 32/scale. |
| 16 | `ambient.direction_generator` | `gaussian_unit_sphere` | Reproducible from the proposal RNG; not an exhaustive sphere design. |
| 17 | `ambient.max_attempts_per_anchor` | `2000` | Preparation/redraw cap, not 2,000 trajectories. Audit feasibility at 500/2,000/5,000 without an engine before signing. |
| 18 | `archive_following.scales` | `[0.025, 0.05]` | Match ambient radii so directional and ambient evidence are comparable. |
| 19 | `archive_following.neighbour_rule` | `k_nearest_low_J_archive_vectors` | Requires an archive `j` column; low J orders proposals and never establishes plausibility. |
| 20 | `archive_following.source_pool` | `[low_j_archive_vectors, confirmed_differences, cross_block_directions]` | Keep only if every source can contribute. Cross-block directions run in their own kernel. |
| 21 | `cross_block.scales` | `[0.025, 0.05]` | Same normalised scales; paired signs are mandatory. |
| 22 | `support.min_confirming_neighbours` | `2` | Operational support label, not a confidence level; report label sensitivity at 1 and 3. |
| 23 | `graph.edge_proposal_rule` | `complete_pairwise_distances` | Distance proposes edges only; it does not establish connectivity. |
| 24 | `graph.tie_breaking` | `candidate_id_ascending` | Deterministic reproducibility choice. |
| 25 | `graph.minimal_edge_algorithm` | `kruskal_minimum_spanning_tree` | Tests exactly `N-1` straight candidate edges for `N` endpoints. |
| 26 | `graph.bridge_fractions` | `[0.25, 0.5, 0.75]` | Costs 60 trajectories/edge on 20 seeds. Four interior fifths would cost 80/edge and is the bounded denser alternative. |
| 27 | `graph.bridge_pass_rule` | `all_declared_probes_must_confirm` | A failure leaves the edge unresolved; it is not proof of disconnection. |
| 31 | `representative_selection.n_per_component` | `2` | Sensitivity 1 and 3. Every extra representative multiplies later scenario/PSA work; it does not alter the NROY set. |
| 32 | `archive_following.quota` | `8` | Derived, not free-standing: 2 retained directions x 2 scales x 2 signs. Must change to 16 if row 34 changes to 2. |
| 33 | `archive_following.neighbour_count` | `3` | Make the first three low-J rows eligible and retain the first usable direction. Sensitivity 2 and 5 has no trajectory cost while row 34 stays 1, but may matter when an earlier row equals the anchor. |
| 34 | `archive_following.max_directions` | `1` per assembled archive source (low-J and confirmed difference) | Honest count cap, not rank. Sensitivity 2/source requires row 32 = 16 and adds 40 discovery trajectories/anchor. Cross-block has its own quota. |
| 35 | `cross_block.quota` | `24` | Six frozen unordered parameter-block pairs x 2 scales x 2 signs. Sensitivity 48 samples two directions/pair and adds 120 discovery trajectories/anchor. |
| 36 | `separation.min_anchor_neighbour` | `1e-4` | Below the 0.025 smallest radius. No-engine feasibility audit at `1e-6`, `1e-4`, `1e-3` before signing. |
| 37 | `separation.min_neighbour_neighbour` | `1e-4` | Same tolerance prevents near-duplicate support inflation. Use the same no-engine audit grid. |
| 38 | `separation.min_direction_classes` | `2` | Matches the two-neighbour rule without allowing one directional family to count twice. Report label-only sensitivity at 1 and 3 from the completed direction labels. |
| 39 | `promotion.variance_alpha` | `0.05` | Direct one-sided 95% upper variance bound for allocation only. Recompute promotion sensitivity at 0.025 and 0.10; final 20-seed acceptance stays nominal. |

### Coupled numeric lock-in

Rows 32--35 cannot be signed independently:

- `max_directions=1` applies separately to the low-J archive and confirmed-
  difference sources, giving two archive-following directions and therefore
  `archive_following.quota=8`.
- This requires at least two distinct confirmed anchors and at least one usable
  non-identical archive vector per anchor. If that precondition is absent,
  change the source pool and quota in a new pre-run plan; do not let a source
  disappear silently.
- The current 33 coordinates are partitioned into four blocks, hence six
  unordered block pairs. One compensating direction per pair gives
  `cross_block.quota=24`. M1 adds `p` to the diagnosis block and does not create
  a fifth block.
- A partial quota is invalid. Boundary-infeasible cells stay rejected and make
  the design unresolved; they are not clipped or replaced.

The separation grid is a **pre-run design feasibility audit**, not permission
to choose the most favourable tolerance after seeing outcomes. A stricter
post-run label sensitivity can reuse completed results; a looser threshold can
introduce previously unrun vectors and may require a separately frozen design.

## B. Backbone-dependent — 8 rows

These rows must not be signed until the 1975-versus-1980 choice is resolved.
The prepared shared config is a minimal mechanical derivative, but that does
not make its inherited source scientifically suitable.

| Row | Plan path | Recommendation now | Required closure |
|---:|---|---|---|
| 1 | `models.m0.calib_space` | Do not sign the current 33-dimensional path yet. | Option A needs a V13.1-compatible space; Option B needs a scientifically restored 1980 V13.22 base and recalibration. |
| 2 | `models.base_config` | Do not sign the 1975 derivative. Prefer backbone Option B if later V13.22 mechanisms are intended. | Bind the exact reviewed 1980 config hash and registry status after the backbone decision. |
| 6 | `p_parameter.name` | Conditionally approve `aids_symptom_annual_probability`. | Confirm it is the final M1 coordinate in the chosen M1 space. |
| 7 | `p_parameter.config_path` | Conditionally approve the proposed `stage_structured_diagnosis` path. | Prove the selected config and `ConfigReader` preserve it; wrong paths may be discarded. |
| 8 | `p_parameter.min` | Approve exact `0.0` conditional on the identity gate. | This is the required M0 nesting boundary, not an estimated lower limit. |
| 9 | `p_parameter.max` | Treat `0.5` as a project proposal, not a literature constant. | Mechanism/clinical review must accept the support. A predeclared `0.25` upper-bound sensitivity is a separate calibration contract, not a post-hoc trim. |
| 10 | `p_parameter.bins` | If max 0.5 is approved, use `[0, 0.1, ..., 0.5]`. | Six boundary probes cost 30 discovery trajectories/anchor. A 0.05 grid has 11 probes and costs 55/anchor. Bins describe coverage, not plausibility. |
| 11 | `p_parameter.routine_hazard_scale_identity_value` | Approve exact `1.0` conditional on route review. | It leaves routine diagnosis unchanged; any other value breaks M0=M1 at `p=0`. |

## C. Pilot-derived budgets — 3 rows

No numeric recommendation is defensible before a separately authorized pilot
on the intended machine profile.

| Row | Plan path | Derivation after the pilot |
|---:|---|---|
| 28 | `budget.max_trajectories` | Freeze anchor/promoted/edge counts, build each exact ledger, and set the cap to cover its unique result keys. Price the total workflow separately; do not treat boundary rejection as free replacement capacity. |
| 29 | `budget.max_attempts` | Start from `max_trajectories` and add a predeclared failure/loss margin supported by pilot and scheduler evidence. Attempts are never refunded. |
| 30 | `budget.wall_time_cap_seconds` | Use measured seconds/trajectory, approved workers, scheduler limit, startup/merge overhead, and a stated safety margin. Hitting the cap produces `PARTIAL`, never a complete parent. |

The priced-budget artifact must bind the signed plan, measured pilot, exact
machine profile, all three caps, currency/credit assumptions, and approval.

## D. Seed-audit-dependent — 3 rows

Choose these only after the audit enumerates every prior experiment, pilot,
confirmation, proposal RNG, and imported artifact source. Reserve them before
outcomes are inspected.

| Row | Plan path | Required choice |
|---:|---|---|
| 40 | `seeds.discovery` | Exactly five unique common-random-number seeds absent from the complete used-seed set. |
| 41 | `seeds.confirmation` | Exactly 20 fresh unique seeds, disjoint from discovery and all prior work. |
| 42 | `seeds.proposal_rng` | One integer distinct from every simulation seed; it controls design reproducibility, not simulator randomness. |

Do not choose a visually convenient consecutive block until the full audit says
it is unused. The plan's `known_used_elsewhere` list is a starting inventory,
not proof of completeness.

## E. Target/uncertainty provenance — 3 rows

| Row | Plan path | Recommendation | Required evidence |
|---:|---|---|---|
| 3 | `target_contract.register_path` | Use `configs/hm_target_register_focused_20260916.json` if source review confirms every construct. | Exact file hash, workbook hash, sheet/range/definition review, units, and extraction path. |
| 4 | `target_contract.frozen_order` | Keep the proposed seven SHM diagnosis then six RIVM AIDS targets. | Confirm the 1996-onward diagnosis and 2000-onward AIDS window decision; order becomes vector identity. |
| 5 | `target_contract.n_targets` | `13`, mechanically conditional on row 4. | Must equal the unique frozen-order length; this is not an independent scientific choice. |

These three rows do not by themselves approve the acceptance denominator.
After the plan is signed, regenerate and review observed values, every variance
component, and `B_i`. Where observation variance is underivable, `V_obs=0` may
be used only with the recorded conservative assumption: it is not zero-error
measurement, it narrows the denominator and biases toward over-exclusion. The
declared 10% discrepancy term is also a project assumption. Any later change to
either requires a new versioned acceptance contract and sensitivity analysis.

## Owner close-out record

For each register row, record one of: `APPROVE AS RECOMMENDED`, `APPROVE WITH
<exact replacement and rationale>`, or `DEFER PENDING <named evidence>`. Then:

1. reconcile every replacement across coupled rows and recompute the complete
   design/cost identities;
2. resolve the backbone and bind exact config/space hashes;
3. regenerate acceptance inputs from the resulting signed plan;
4. obtain the seed audit, identity gate, pilot, and priced-budget artifacts in
   gated order; and
5. run no-engine validation and independent review before requesting any
   production dispatch authorization.

This packet is intentionally not a signature surface. The authoritative
decision values and sign-off remain in the versioned plan and decision
register.
