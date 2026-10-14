# Implementation note: focused stage-structured diagnosis calibration

> Archived under `documentation/reports/calibration/`; original filename retained.

Date: 2026-09-22. Brief: `TASK_BRIEF_STAGE_STRUCTURED_DIAGNOSIS_FOCUSED_CALIBRATION_2026-09-22.md`.
Status: implemented, unit-tested with synthetic fixtures and mocked engines, validate-only
passed. **No engine was constructed and no trajectory was run by the implementing agent.**
The user launches every simulation (see `USER_RUN_COMMANDS.md`).

Production configuration and HTA baseline are unchanged.

## Code changes

| File | Change |
|---|---|
| `stage_structured_diagnosis.py` | Optional `symptom_start_year`, validated as a finite calendar year in [1900, 2100] (booleans rejected). `symptom_probability_at(year)` returns 0 strictly before the boundary and the configured value at or after it. With no boundary declared, the symptom hazard is active at all times: these are the semantics the completed 240-trajectory audit ran under, kept so that audit stays reproducible. |
| `run_v3.py` | `_step_stage_structured_diagnosis` passes `profile.symptom_probability_at(current_year)` as the AIDS symptom probability. The routine hazard, the MSM multiplier, the RNG process keys, the identity dispatch (`rho=1, p=0` → legacy branch) and C-1 are untouched. The start-up banner prints the boundary. |
| `configs/stage_structured_diagnosis_focused_calibration_20260922.json` | New frozen plan: backbones, focused coordinates and ranges, grids, seeds, budgets, time caps, predeclared decision thresholds. |
| `run_stage_structured_diagnosis_focused_calibration.py` | New harness: validate, prepare, smoke, stages A/B/C, shortlist freeze, report, orchestrate. |
| `pytest_no_engine_guard.py` | Pytest plugin that skips any test that would construct a real `SimulationEngineV3`, so that existing suites can run inside the execution boundary. |
| `tests/test_stage_structured_diagnosis_focused_calibration.py` | 37 new tests (synthetic or mocked; none constructs an engine). |

Config path of the new field: raw JSON
`transmission_model.disease.diagnosis.stage_structured_diagnosis.symptom_start_year`
(`{"value": 1985}`), engine `disease.diagnosis.stage_structured_diagnosis.symptom_start_year`.

The harness always writes the boundary (1985), and validation refuses a configuration without it.

## Design as implemented

- **Backbones** 1326 and 1788 are injected through the production path. They share identical diagnosis parameters (base anchors 1985/1990/1997/2019 and MSM multipliers 0.7395/0.4837/0.6957) and differ elsewhere.
- **Stage A**: 6 rho × 5 symptom values × 2 backbones × seeds 201010–201014 = 300 trajectories. Only the stage block differs from the backbone.
- **Stage B**: 128 vectors per backbone × seeds 201020–201024 = 1,280 trajectories, with `rho = 1` fixed. Only the three multiplier anchors and the symptom probability differ from the backbone; this is checked leaf by leaf, and the base schedule is checked byte for byte.
  - Each surgical Stage-B configuration hashes identically to a re-injection through `run_lhs_calibration.inject_params`, and all 256 are checked.
  - Vector 0 is the identity structural point, and the three warm proposals come first:
    1. identity;
    2. candidate multipliers with AIDS = 0.35;
    3. the prior ρ=0.80/AIDS=0.35 effective path, represented at the anchors with ρ=1 as `m' = (1-(1-p_b m)^0.8)/p_b` = 0.6088/0.3953/0.5794.
  - That representation is exact at the anchors, deviates by at most 0.0006 in 1996–2025 (from interpolation), and **cannot** carry ρ<1 before 1996, where the neutral multiplier 1.0 applies: the maximum deviation there is 0.058. Warm proposals are never clipped; an out-of-range proposal is rejected with its reason.
  - The remaining 125 vectors per backbone come from a reproducible maximin LHS: seed 201060, best of 2,000 designs, distances including the warm points.
- **Stage C**: at most 10 tuples × seeds 201030–201049 (at most 200 trajectories). One **backbone-identity comparator per backbone is included inside the ten**, so that "improves relative to its backbone" is a paired same-seed comparison on fresh seeds. At most 8 mechanism tuples are selected in this order:
  1. best 2024 primary pass;
  2. best balanced (lowest J) primary pass;
  3. each backbone's best pass;
  4. lowest-J pass per symptom bin [0, 0.1), [0.1, 0.25), [0.25, 0.5];
  5. farthest-point diversity on (2024 error, AIDS-after-2015 R, symptom);
  6. near misses only after passes are exhausted.

  Duplicate effective MSM routine paths are excluded among mechanism tuples.
- **Seeds** are mutually disjoint. They were checked against 317 manifests, 61 config plans, the declared foreign ranges, and the coupled-search seeds: no overlap. The 201000 block is new.
- **Scoring** is unchanged: the `coupled_calibration_search` four-group search statistic ranks, and target-by-target `I_max ≤ 3` at 10% discrepancy accepts (20% is reported as sensitivity only). The 13 targets and the AIDS observation function are unchanged.

## Time stop

**Cap raised by user decision (2026-09-22).** The brief's 45/42-minute cap assumed ~8 s per
trajectory. A first Stage-A attempt, interrupted by the user after about 20 s, measured
16.1–16.5 s per trajectory at 8 workers: ~60 minutes for 1,780 trajectories, which would have
cut Stage B short and made Stage C impossible. The cap is now 75 minutes hard, with new work
stopping at 72 (the same 3-minute margin). The interrupted folder (8 journaled
trajectories) was deleted before any result was inspected, and the run is re-prepared from
scratch. Budgets, seeds, targets, design and thresholds are unchanged. The mechanics below
are otherwise as described, with 4,320 s and 4,500 s in place of 2,520 s and 2,700 s.

The 45-minute cap is implemented as **cumulative active dispatch time**, starting at the first Stage-A dispatch and summed across invocations. The pause while you review the shortlist does not count; without that rule, the required review before Stage C would exhaust the wall clock.

New work stops at 2,520 s. At 2,700 s, in-flight work is abandoned: the worker processes are terminated and the lost tasks are recorded. A stage that stops on time is marked `PARTIAL_TIME_LIMIT`. The shortlist can be frozen only from a COMPLETE Stage B. An incomplete design can never be labelled "not supported".

## Resume and overwrite rules

- `--prepare` refuses an existing directory.
- Every stage invocation re-verifies the frozen plan hash, the design hash, the rebuilt configuration hashes, and the hashes of all runtime modules (including this harness and the mechanism). Any drift is refused.
- A COMPLETE stage is never rerun.
- A stage with journal entries needs `--resume`.
- A stage containing failed trajectories stops. Failures are never retried or replaced.
- Stage C additionally needs `--shortlist-sha256` equal to the frozen shortlist's hash. This is the explicit record that the shortlist was reviewed.
- Trajectories interrupted without a result (a crash, or lost at the hard cap) are re-dispatched on resume. Under common random numbers this is a re-execution, not a stochastic retry.

## Per-trajectory checks (a failure stops that trajectory and is preserved)

- The three stage counters equal endogenous diagnoses for total, MSM and non-MSM, in every year 1975–2025.
- The AIDS proxy equals its two components.
- Zero AIDS-stage diagnoses before 1985, whenever the routine path is also zero before 1985 (true for both backbones). The 1985–1987 AIDS-stage count is recorded to show the boundary activates.
- The post-2015 undiagnosed-MSM stock-flow ledger closes exactly.

The report also checks that exogenous non-MSM diagnoses are identical across tuples for the same backbone and seed.

## Report-classification bug (fixed)

In `run_stage_structured_diagnosis_audit.py::_write_report`, "informative" required a profile that passed every gate in both candidates. That set is the same as "promising", which routes to "supported", so the "informative" branch was unreachable. The narrative text was also hard-coded.

The new harness uses a pure `classify_decision` with the brief's three rules. The rules are evaluated per clause (c1–c7) against predeclared thresholds in the plan, and each label is covered by a distinct unit fixture. A partial design can never be classified "not supported".

The old runner is left byte-unchanged so that the completed audit's recorded `runner_sha256` stays valid. Its label ("not supported") should be read in light of this bug and of the brief's own reinterpretation.

## Predeclared decision thresholds (frozen before any result)

- **Material 2024 improvement**: the confirmed |error| is at least 25% lower than the paired backbone's, and at least 15 of the 20 paired seeds improve.
- **Materially worse AIDS**: the absolute error rises by more than 10% of the observed target. The clause fails only when both 2019 and 2022 are materially worse.
- **Earlier diagnosis acceptable**: R(diagnoses 1996–2015) is at most 1.10× the comparator's, and all five earlier targets have implausibility ≤ 3.
- **Pathology flags**: a change of more than 10% in HIV deaths or MSM infections (increase), or in the 2025 undiagnosed-MSM stock or ART stock (either direction), or any negative stock. These are flagged for decision review and prevent an automatic "supported".
- **Boundary point**: any coordinate within 2.5% of its range from a bound.
- **Nearby region**: at least 2 discovery neighbours within normalised distance 0.35, with at least 50% of them sharing the tuple's behaviour (discovery primary pass and better 2024 error than the discovery identity).
- **Late presentation**: 32.1% (238/742) is a one-sided upper bound on construct 1, never an equality target. 208/742 = 28.0% is also reported as an arithmetic ceiling. The pooled 208/1,301 figure is cross-validation context only.

## Tests run by the implementing agent

1. `.venv/bin/python -m pytest -p pytest_no_engine_guard -rs -q tests/test_stage_structured_diagnosis_focused_calibration.py`: **37 passed**. This includes a full synthetic A→B→freeze→C→report pipeline driven by a fake worker, and a time-stop test.
2. `.venv/bin/python -m pytest -p pytest_no_engine_guard -rsf -q` over the 11 existing relevant suites (audit, AIDS observation, counter-based RNG, C-1, diagnosis year bands, MSM relative diagnosis, non-MSM diagnosis, temporal diagnosis audit, stock-flow decomposition, coupled search, forecast confirmation): **232 passed, 12 failed, 19 skipped**.
   - **Failures**: all 12 are the frozen-provenance guard (`code changed since the search: ['aids_observation.py', 'calib_extractor.py', 'run_v3.py']`) in the temporal-audit, stock-flow and forecast-confirmation runners. They fail identically with this session's changes reverted, so they predate this work; the prior audit reported the same 12. They are **not** passes.
   - **Skips**: the 19 skipped tests construct a real engine. They are listed as a user command in `USER_RUN_COMMANDS.md`. One of them, `test_enabled_with_multiplier_one_matches_baseline`, is the documented known exception E-2026-09-17-01.
3. `--validate-only` passed: 0 engines, 0 simulations, 0 artifacts. It checked 60 Stage-A and 256 Stage-B configurations, and every design check held.
