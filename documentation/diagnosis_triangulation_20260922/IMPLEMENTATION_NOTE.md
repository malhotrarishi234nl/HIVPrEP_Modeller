# Implementation note: diagnosis-mechanism triangulation and staged orchestration

Date: 2026-09-22. Brief: `TASK_BRIEF_DIAGNOSIS_TRIANGULATION_ORCHESTRATION_2026-09-22.md`.

**Status: prepare-only.** The implementing agent ran only four things:

- the static evidence audit;
- the unit tests (synthetic fixtures, a mocked engine and a fake worker);
- `validate-plan`, under an engine guard;
- nothing else: **no engine was constructed and no trajectory was run.**

**Unchanged:** the production configuration, the source workbook (its hash is checked before and after reading), the previous calibration outputs and the HTA baseline.

## Files

| File | Role |
|---|---|
| `run_diagnosis_triangulation_orchestration.py` | Master CLI. Subcommands: audit-evidence, validate-plan, smoke, benchmark, discover, merge-discovery, freeze-shortlist, confirm, merge-confirmation, report, status, resume, run-all. |
| `configs/diagnosis_triangulation_orchestration_20260922.json` | The frozen plan. |
| `documentation/diagnosis_triangulation_20260922/` | Authored sources of `AIDS_DEFINITION_ADJUDICATION.md`, this note and `USER_RUN_COMMANDS.md`. audit-evidence copies them into the output root and hashes them. |
| `tests/test_diagnosis_triangulation_orchestration.py` | 41 tests. |

The focused-calibration harness (`run_stage_structured_diagnosis_focused_calibration.py`) is **imported as a library and not edited**. Reused unchanged:

- configuration builders;
- the effective-path arithmetic;
- the per-trajectory identity and 1985-boundary checks;
- the scoring (the frozen 13-target contract);
- the bounded dispatcher;
- local sensitivity.

Because the harness is untouched, the completed focused run keeps its recorded runtime hashes.

**No existing file was modified.**

## Output root and provenance chain

`outputs/calibration/diagnosis_triangulation_orchestration_20260922/` has a single `manifest.json`. It pins:

- the plan hash and the frozen-plan hash;
- the design hash and the validation hash;
- evidence-file hashes: register, mapping, target contract, adjudication checks, authored docs;
- source hashes: workbook, RIVM vetted candidate, the prior focused run's manifest, frozen plan, design and confirmation summary;
- runtime module hashes: the `rcc.RUNTIME_MODULES` simulator set plus this module, the focused harness, the audit runner, the ledger module and `stage_structured_diagnosis.py`;
- the seeds;
- unit status, gates and the shortlist hash.

Every execution command reloads and re-verifies all of these. Any drift refuses to run.

Integrity rules:

- **Journal records.** Every journal record carries the plan and design hashes. A merge refuses mismatched, duplicate or failed trajectories.
- **Writes.** Evidence and freeze outputs use write-once semantics: identical content is an idempotent no-op; different content refuses. Mutable state files (manifest, shard state, clock) use atomic replace.
- **Status source.** Unit state lives in each unit's `state.json`. The manifest summary line is convenience only; `status` recomputes from the journals.

## Design as implemented

### Models

- **M0** is the backbone configuration with **no** `stage_structured_diagnosis` block. That is the legacy structure itself, and `check_design` verifies it equals the backbone exactly.
- **M1** is `fc.stage_b_config`:
  - `routine_hazard_scale = 1`, 1985 symptom boundary;
  - only the three MSM anchors and the symptom probability differ from the backbone (checked leaf by leaf);
  - the base schedule is byte-identical.
- **Candidate reconstruction.** The three benchmark candidates and all discovery anchors are rebuilt from the prior focused design and **hash-checked** against its recorded configuration hashes.

### Stage 1: benchmark (150)

Five tuples × 30 fresh seeds (202100–202129):

- M0_1326;
- M0_1788;
- B_1326_v002 (balanced);
- B_1788_v123 (balanced);
- B_1788_v048 (aggressive).

### Stage 2: discovery (1,920)

Per backbone, 192 vectors × 5 seeds (202200–202204):

- **Identity (M0).**
- **Anchors (3):** the prior Stage-C mechanism tuples of that backbone that passed the primary contract on fresh seeds:
  - 1326: v002, v057, v040;
  - 1788: v123, v057, v048.
- **Inner box (124):** a maximin Latin hypercube in an L∞ box of half-width 0.125, in coordinates normalised to the authorized ranges (±0.25 multiplier, ±0.0625 symptom). It is centred on the balanced candidate (1326: v002; 1788: v123).
- **Outer ring (64):** greedy maximin from a seeded uniform pool in the half-width-0.30 box minus the inner box.
- **Clipping.** All boxes are clipped to the authorized ranges; no range is adapted after outcomes.
- **Interpretation.** "Neighbourhoods around the two balanced candidates" is read as one neighbourhood per backbone around that backbone's balanced candidate.

**Shards.** Eight deterministic shards: shard k covers backbone k mod 2 and slice k div 2.

- The identity and anchors sit in shards 0 and 1, so a completed prefix always contains the paired comparator.
- Shards 0 and 1 hold 255 trajectories; shards 2–7 hold 235.

### Stage 3: confirmation (≤360)

- **Shortlist size.** At most 12 tuples: the two discovery M0 identities (paired comparators on confirmation seeds) plus at most 10 M1 finalists.
- **Seeds.** 30 fresh seeds (202300–202329).
- **Shards.** Two shards: (tuple, seed) pair i goes to shard i mod 2.
- **Eligibility.** Discovery primary passes, excluding the benchmark tuples, which are already confirmed on 30 fresh seeds.
- **Selection order** (predeclared):
  1. per backbone: best 2024 error;
  2. per backbone: lowest search J;
  3. per backbone: best latent-compatible pass (E1 and E4 inside on discovery means);
  4. per backbone: best pass with at most +5% infections vs identity;
  5. farthest-point diversity on (u, 2024 error reduction, infection change).
- **Exclusions.** Duplicate effective (routine path, symptom) pairs are excluded. Near misses are used only when passes run out.

### Other seeds

- Smoke seed: 202500. Design RNG seed: 202400.
- All are disjoint from each other and from every seed recorded in 318 manifests, 62 plans and the declared foreign ranges.

### Time

- **Clock.** The measure of the union of wall-clock intervals of all dispatching invocations (heartbeat files under `clock/`). Concurrent shards are therefore not double counted, and review pauses do not count.
- **Caps.** Dispatch stops at 6,600 s; in-flight work is abandoned at 6,900 s; the total cap is 7,200 s.
- **Confirmation reserve.** Discovery shards reserve the estimated remaining confirmation time: remaining trajectories × median benchmark trajectory duration / 8 × 1.10. A shard that cannot fit does not start (`NOT_STARTED_TIME_RESERVE`). A running shard stops dispatching at the reserve line (`PARTIAL_TIME_LIMIT`).
- **Partial discovery.** It can be merged only with `--allow-partial-discovery` and only as a prefix of complete shard pairs. A partial design can never be classified "unsupported".

## Evidence audit (static)

### Register

`empirical_constraint_register.csv` has 371 rows with the brief's 18 columns. It is parsed programmatically from:

- the workbook, read-only: SHM Table 1.1, ECDC AIDS/HIV, UNAIDS infections/PLHIV/AIDS deaths/ART, RIVM-D76;
- the RIVM newest-vetted candidate JSON;
- the transcribed literature rows (PDF pages given; PDF hashes pinned).

**Frozen contract.** Exactly 13 rows are `primary`. The frozen contract is unchanged:

- diagnoses: 1996/2000/2004/2010/2015/2019/2024;
- AIDS proxy: 2000/2005/2010/2015/2019/2022.

**Workbook vs later decisions.**

- **MSM AIDS 2015.** The workbook D76 value is 53 (explicitly incomplete); the frozen target uses 125 (D79). This is recorded in `overlap_warning`, with the audited decision applied.
- **ECDC early AIDS.** This series conflicts with RIVM, so it is retained raw and not used.
- **Deaths among HIV patients.** These are labelled all-cause, not AIDS deaths.

**Tier 2.** UNAIDS/Spectrum series and the SHM ECDC-tool estimates never enter a likelihood.

### Observation mapping and adjudication

- `observation_mapping.csv` keeps the brief's seven constructs separate.
- **AIDS definition adjudication** (`AIDS_DEFINITION_ADJUDICATION.md`):
  - the compartment is **clinical AIDS**, with CASCADE progression;
  - mortality is Glaubius CD4 <200 bands;
  - costs are the Popping "very late" CD4 <200 tier;
  - `hta_report.py` labels the compartment "CD4 <200" (recorded, not edited);
  - diagnosis in AIDS is therefore a **narrower proxy** for advanced presentation, and CD4 <200 without AIDS is structurally unobservable.
- **Machine-checked facts.** `adjudication_checks.json` holds 26 checks. `validate-plan` refused to freeze until all passed; all pass.

### Predeclared external checks

These are in `frozen_target_contract.json`.

| Id | Metric | Evidence | Type | Decision role |
|---|---|---|---|---|
| E1 | MSM infections 2024 | SHM/ECDC tool 310 (250–365) | interval | latent |
| E4 | MSM undiagnosed end-2024 | SHM/ECDC tool 805 (690–900) | interval | latent |
| E2, E3, E5 | MSM infections 2020, 2010; undiagnosed end-2010 | SHM | interval | report only |
| E6 | MSM PLHIV end-2024 | 15,525 (15,415–15,625) | interval | report only |
| E7 | MSM on ART end-2024 | ≤ 14,174 ever started ART | one-sided | report only |
| E8, E9, E9a, E10, E11 | ART coverage; UNAIDS PLHIV, infections, ART, AIDS deaths 2023 | Tier 2 | descriptive / interval | report only |
| E12 | HIV deaths among diagnosed, all groups, 2010–2014 | ≤ 750 (D76 all-cause deaths among HIV patients) | one-sided | mortality |
| E13 / E13f | MSM+trans diagnoses in clinical AIDS, 2024 | ≤ 58 (56 MSM + 2 trans); fraction ≤ 0.21 | one-sided | advanced |
| E14 | MSM fraction diagnosed in AIDS, 1996–2014 | ≤ 0.287 (Op de Coul) | one-sided | advanced |

**Disclosure.** These checks were declared before any trajectory of this design. However, the implementing agent had read the completed focused run beforehand, including its 2025 undiagnosed MSM stocks of about 280–805, when it chose E4. The E1 and E4 bands are the **published 95% intervals**, not tuned tolerances.

## Decision rules (pure `classify`, one unit fixture per label)

A fresh-seed M1 tuple (benchmark or confirmation), paired with the M0 of its backbone on the same seeds, is **robust** when all four hold:

- **c1:** primary pass (I_max ≤ 3 at 10%);
- **c2:** 2024 |error| reduction of at least 25%, with ≥ 75% of paired seeds improved (≥ 23/30);
- **c3:** R(diagnoses 1996–2015) ≤ 1.10 × M0's, and all five earlier diagnosis targets I ≤ 3;
- **c4:** E12 not violated and no negative stock.

Further clauses:

- **c5, latent status.** From E1 and E4:

  | E1 | E4 | Status |
  |---|---|---|
  | inside | inside | compatible |
  | inside | outside | time_to_diagnosis_mismatch |
  | above (neither below) | — | expansion |
  | below (neither above) | — | contraction |
  | above and below | — | mixed |

- **c6:** E13, E13f and E14 not violated. This clause can only reject, so it may pass vacuously.
- **c7:** at least 2 discovery neighbours within normalised radius 0.35, with ≥ 50% sharing the behaviour (primary pass and better 2024 error than the discovery identity).
- **c8:** no coordinate within 2.5% of an authorized bound or of an interior face of the discovery box.
- **c9:** local sensitivity (40 neighbours) full rank, stable, condition number ≤ 30, and symptom column R² on the routine columns ≤ 0.90.

Rules, applied in order:

- **R0.** The design is incomplete → no final classification. A provisional label is shown, and "unsupported" is impossible.
- **R1.** No robust tuple → **Unsupported in the tested form.**
- **R2.** Every robust tuple has latent status "expansion", and some robust tuple satisfies c6 → **Promising but underconstrained**, flagged `tier2_expansion`. The decision records each E1/E4 model mean, the Tier-2 interval and the excess over its upper limit (`latent_expansion`), and the reason text states them. E1 and E4 are SHM ECDC-tool estimates, partly fitted to the same diagnoses. They are plausibility checks, not independent evidence, so they never reject on their own. (Revision 2026-09-22: this rule previously returned Unsupported.) The brief allows Unsupported when improvements "consistently arise from empirically implausible epidemic expansion". Here "empirically implausible" is read as requiring Tier-1 evidence. No Tier-1 constraint on MSM incidence or the undiagnosed reservoir exists in the register, so that branch cannot fire in this design.
- **R3.** Some robust tuple satisfies c5–c9 → **Supported as a preliminary structural improvement.**
- **R4.** Some robust tuple is latent-compatible with c6, but fails region, boundary or ridge → **Promising but underconstrained.**
- **R5.** Some robust tuple has latent status time_to_diagnosis_mismatch with c6 → **Unresolvable with current disease states.** Incidence agrees but the reservoir does not. The discriminating construct is the time-since-infection / CD4-at-diagnosis distribution.
- **R6.** Otherwise → Promising if any robust tuple has c6 (with `tier2_expansion` and the magnitudes added when the leader's latent status is expansion). Otherwise Unsupported, because every robust tuple violates the Tier-1 advanced-presentation bound.

**Tier-2 principle.** Only Tier-1 clauses (c1–c4, c6) can lead to **Unsupported**. A unit test checks that every latent status, with the Tier-1 clauses passing, gives a label other than Unsupported. R5 (Unresolvable) still depends on the Tier-2 E4 interval. It is a diagnostic label, not a rejection, and the report states that it depends on Tier-2.

## Decomposition

`stage_diagnosis_decomposition.csv` holds the paired change in 2015–2024 MSM diagnoses. It uses the exact ledger identity for the undiagnosed MSM stock:

D = U₂₀₁₅ + I − U₂₀₂₅ + other

That gives four additive terms:

- (a) a larger reservoir carried into 2015;
- (b) additional infections;
- (c) the reservoir left at 2025 (a timing/retention proxy);
- (d) other exits.

Cross-cutting splits, reported alongside:

- the change by diagnosis stage;
- **symptom-attributed AIDS-stage diagnoses**: an expected-hazard attribution, λ_s/(λ_r+λ_s) by quarter, labelled as accounting;
- downstream changes in ART, deaths and PLHIV.

"Timing with infections held fixed" is **not identifiable** from saved draws, because no counterfactual run exists. The report says so.

## Tests run by the implementing agent

- `.venv/bin/python -m pytest -p pytest_no_engine_guard -q tests/test_diagnosis_triangulation_orchestration.py`: **41 passed, 0 failed, 0 skipped.** Coverage:
  - evidence parsing, including reconciliation, NULL handling, the D76 cumulative check and vetted-candidate filtering;
  - the register against the real workbook, read-only, with the hash checked;
  - plan budgets and seed disjointness;
  - design reproducibility and coordinate isolation;
  - geometry and sharding;
  - union clock, latent-status mapping, external-check statuses and the ledger/decomposition identities;
  - all four labels plus the incomplete case;
  - idempotent audit/validate;
  - refusal to freeze on a failed adjudication;
  - write-once semantics;
  - plan/code drift refusal;
  - gates (discovery needs the benchmark; confirmation needs the frozen shortlist and its hash);
  - time stop and resume dispatching only missing pairs;
  - the confirmation-reserve refusal;
  - merge refusal on duplicate and mismatched trajectories;
  - partial-prefix merge;
  - a full synthetic benchmark → 8 shards → merge → freeze → gate → confirm → merge → report pipeline via `run-all`;
  - the new observers on a mocked engine.
- Existing suites were **not re-run**: no existing file was changed.
- `audit-evidence` and `validate-plan` were run on the real output root. Both constructed 0 engines and started 0 simulations. Validation computed **2,430** maximum trajectories (150 + 1,920 + ≤360), about 83 minutes at 8 workers.

## Not done (by design)

- No benchmark, discovery, confirmation or smoke trajectory was run.
- **M2 is described, not built** (`AIDS_DEFINITION_ADJUDICATION.md`, final section). No stored source bounds the under-tested fraction.
- The "CD4 <200" labels in `hta_report.py` are not corrected; this is recorded for a later HTA-facing task.
- No conversion coefficient between clinical AIDS and CD4 <200 is fitted.

## Known limitations of the harness

- **Real trajectory path.** `run_trajectory` and its observers cannot be exercised here without an engine. The observers are unit-tested on a mocked engine. The optional user-run `smoke` command (2 trajectories, separate directory) checks the real path before the benchmark.
- **Concurrent shards.** These are supported, but sequential running is recommended. Each invocation computes its remaining time at start, so two concurrent shards may both start near the stop line.
- **Manifest updates.** Every read-modify-write of `manifest.json` (unit summary lines, merge, freeze and report gates, `STATUS.md`) holds an exclusive `fcntl.flock` on `<root>/.manifest.lock`. Concurrent shards therefore cannot lose each other's updates. A second, non-blocking per-unit lock (`<unit dir>/.running.lock`) refuses to start a shard that is already running in another shell. Both locks are released by the OS if a process dies. Unit tests cover both (four spawned processes × 15 locked increments; a second-invocation refusal). (Revision 2026-09-22: previously last-writer-wins.)
