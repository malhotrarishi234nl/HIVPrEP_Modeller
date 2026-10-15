# Option B 1980 recalibration: preparation-only compute plan

Status: **PREPARATION_ONLY_NOT_AUTHORIZED**. This document and the bound plan are
validation artifacts, not permission to prepare artifacts, construct an engine, spend compute,
or claim calibration evidence.

## Exact proposed design

- Plan: `configs/coupled_search_plan_option_b_1980_recalibration_preparation_20261015.json`.
- Model start and extraction start: 1980. Extraction ends in 2025. The runner checks the
  plan against the hash-bound config and will not silently substitute the historical 1975 start.
- Search space: the reviewed 33-dimensional M0 space, with no transformation or bound change.
- Evidence: 13 targets in the frozen plan order (seven SHM MSM diagnosis anchors and six RIVM
  MSM AIDS anchors). The production target register also verifies the exact source workbook hash.
- Proposal sources: historical source designs 620, 621 and 548, followed by the declared warm
  CSV in file order, capped at 24 accepted warm vectors; the rest is constrained LHS.
- Historical 1975 vectors are proposal seeds only. Every vector must be simulated and rescored
  under Option B. No prior score, fit label, pass/fail result or acceptance status carries forward.
- Search maximum: 256 initial vectors plus 28 batches of 64, five discovery replicates each;
  at most ten frozen-shortlist candidates receive 20 fresh confirmation replicates. Together with
  three paired smoke checks this is at most 10,446 full trajectories.
- Hard stops remain: 200 differential attempts per slot, 100,000 raw global proposals per batch,
  a 21,600-second wall cap, no scientific early stopping, bounded in-flight completion on the cap,
  and `PARTIAL_TIME_LIMIT` rather than a completed-negative interpretation.

The no-engine feasibility audit supports the existing 200-attempt setting for the actual 33-D
space and rejects using 500 as an operational cap for the later robust-NROY proposal kernel: the
worst audited M1 boundary case required 479 attempts. The robust-NROY plan therefore retains its
separate 2,000-attempt cap. These are different algorithms and their caps must not be conflated.

## Deliberately open decisions

The plan carries five discovery, twenty confirmation, three smoke and one proposal-RNG seed
placeholder. They are intentionally unsigned and cannot pass validation, preparation, or any
engine stage. Exact integer seeds must be selected only after the seed-disjointness audit is
complete. The plan itself also allows only `validate-only`; changing that status requires an
explicit owner decision and creates a new exact plan hash.

Scientific review must still sign the exact Option B config, 33-D parameter space, 13-target
register/order and variance assumptions. In particular, `V_obs=0` for underivable target terms is
a declared model assumption that removes an uncertainty contribution and can bias the screen
toward over-exclusion. It is not an empirical estimate.

## Minimal staged authorization bundle

1. **Authorize preparation, not a search.** Resolve the seed audit, replace every placeholder,
   sign the exact scientific inputs, and approve the resulting exact plan hash. A validation pass
   may then authorize only preparation and the bounded three-pair identity smoke check.
2. **Require identity before calibration spend.** The paired smoke artifact must show identical
   production and observing-harness outputs/states under Option B. Any mismatch stops the chain.
3. **Price from a measured pilot.** Use the smoke timing and a representative worker benchmark to
   record throughput, worker count, wall-time expectation and priced compute ceiling. A later
   owner authorization must name the exact preparation, smoke and benchmark hashes before the
   10,240-trajectory discovery budget can be dispatched. No unpriced or final production dispatch
   is bundled here.
4. **Confirm only after a complete search.** A `SEARCH_COMPLETE` artifact freezes the shortlist.
   Fresh 20-seed confirmation is authorized only from that exact hash; partial/failed searches do
   not advance.
5. **Build M1 only from positive Option B evidence.** A confirmed M0 anchor is rerun through the
   exact M1 construction and p=0 identity gates. Positive-p feasibility/confirmation follows only
   if those gates pass. The robust-NROY production dispatch remains a separate, later, priced and
   hash-bound authorization.

This sequence bundles the mechanical path now while preserving the distinction between code
acceptance, calibration evidence and production authorization.
