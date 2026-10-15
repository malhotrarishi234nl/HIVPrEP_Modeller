# Bayesian History Matching — Build Specification

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** This earlier method build
> record is not the current operating specification. It remains at its original
> path because frozen inputs reference it. Start at `documentation/INDEX.md`.

**Created:** 2026-09-10
**Status:** **HM-1–HM-4 complete. Two bounded post-fix stochastic learning waves completed on 2026-09-15; HM-6 is not yet frozen.**
**Build report:** `outputs/calibration/hm_prototype_20260910/HM_BUILD_REPORT_2026-09-10.md` — read it before acting on this spec; it corrects three things below and records findings that change what the method can claim.
**Corrections applied to this document on 2026-09-10 are marked `CORRECTED 2026-09-10`.**
**Replaces:** the ABC-SMC / SNPE calibration stack as the production calibration method
**Expands:** Track B (B-1 … B-7) of `documentation/archive/historical_workstreams/phase2/PHASE2_AGENT_HANDOFF_2026-09-09.md` into an executable build

> **CURRENT IMPLEMENTATION ADDENDUM — 2026-09-15.** Sections explicitly labelled “as of
> 2026-09-10” below are retained as the historical build record and are not current operating
> instructions. The current practical guide is `documentation/guides/HISTORY_MATCHING_GUIDE.md`.
> C-1 is enabled; the stochastic CRN estimator and A-5 `V_ensemble` are implemented; the 19-anchor
> target contract is declared; and the runner now supports checksum-verified focused continuation.
> Wave 1 evaluated 1,000 vectors and retained 46 primary NROY members. Focused Wave 2 evaluated
> 974 vectors and retained 90 primary members while reducing the bounding volume from 7.05% to
> 1.42% of the original box. Only two Wave 2 vectors survived both the 10% and 20% target-relative
> sensitivities. Those vectors are awaiting many-seed confirmation and are not a frozen ensemble.

---

## 0. Read first

1. `documentation/archive/historical_workstreams/phase2/PHASE2_STRATEGY_BRIEF_2026-09-06.md` — §3.4 (why UNAIDS targets are not independent evidence), §6 (why history matching), §12 (rejected paths).
2. `documentation/archive/historical_workstreams/phase2/PHASE2_AGENT_HANDOFF_2026-09-09.md` — §1A is the verification, code-review and academic-integrity standard. **It applies to every task in this document.** §1A.4 (the tuning prohibition) is the single most important section for this build.
3. `outputs/calibration/target_reachability_audit_20260906/TARGET_REACHABILITY_AUDIT.md` — which targets are reachable, which are landmines, and which parameter drives which target.
4. **Andrianakis et al. 2015**, *PLoS Comput Biol* 11(1):e1003968 — https://journals.plos.org/ploscompbiol/article?id=10.1371%2Fjournal.pcbi.1003968 — the method, applied to an HIV transmission model with 22 parameters and 18 outputs. This build is a close analogue at 27–32 parameters and ~8 target series. **Read this before writing code.**

### Hard prerequisites — do not start the production build until all are true

> **Status 2026-09-10: none of the four is met.** `run_history_matching.py` enforces this — it refuses
> to run without `--acknowledge-prerequisites` and prints these four as the reason. The prototype (§9)
> and the HM-1/HM-2/HM-4 machinery proceeded, as this section permits.

- [ ] **B-1 settled.** One canonical calibration space. `configs/calib_space.json` declares 27 parameters; `calib_space_v13_15_1975_orientation_seed_abc.json` declares 32; ~~the rank001 particle carries parameters in neither cleanly.~~ The prior box **is** the space you are excluding from, so an unresolved box invalidates the entire NROY set.
  > **CORRECTED 2026-09-14 (B-1).** rank001 lies **inside both boxes on every parameter** (27/27 and 32/32, zero outside) — the "neither cleanly" claim was wrong. The two spaces share a 20-parameter core with **0 role conflicts**; 7 are A-only (`prep_oral_*`, constrained by no registered target), 12 are B-only (natural history + seed composition + `msm_diag_mult_*`). Two bounds conflict: `seed_msm_fraction` (nested) and **`seed_prevalence` (non-nested, previously undocumented)**. The unresolved blocker is the **start year**: A targets a 1980 production config that **does not exist** — all five live V13.x base cases start 1975 and are self-labelled `"diagnostic-only"` — and `run_history_matching.py` has no guard that space and config agree. Analysis: `outputs/hta_results/b1_b2_calibration_space_20260914/B1_B2_CALIBRATION_SPACE.md`.
- [ ] **B-2 settled.** Every parameter classified fixed / calibrated / PSA-only, reconciled against config tier tags, bounds and distributions.
- [ ] **C-1 landed.** Exogenous non-MSM forcing changes the target set — `new_diagnoses_non_msm` leaves calibration entirely once it becomes an observed input. Building the target register before this lands means rebuilding it after.
- [ ] **Post-fix code.** A-11 proved that pre-2026-09-06 `demography.py` was materially defective. Every simulator evaluation used in a production wave must come from post-fix code. **All existing particle CSVs are pre-fix and cannot be carried into a production NROY set.**

The prototype (§9) may proceed before these; the production build may not.

---

## 1. What this replaces, and what changes conceptually

**History matching is not a weighting scheme for ABC. It replaces ABC.**

| | ABC-SMC (current) | History matching (target) |
|---|---|---|
| What it is | Approximate Bayesian **inference** | Parameter-space **exclusion** |
| Output | Weighted sample approximating a posterior | Unweighted **set** — Not Ruled Out Yet (NROY) |
| Accept rule | distance < ε, ε shrinking per generation | implausibility I(x) > 3 → discard |
| Weights | Importance weights correcting the proposal | **none** |
| Claim | "this is the posterior" (only if validated) | "these are not ruled out by the data" |

**Why the switch** (full reasoning in brief §6): ABC targets a posterior that cannot be validated here — 27–32 parameters against ~8 targets, an ε that never accepts, and SNPE returning zero samples. More fundamentally, Bayesian inference conditions on the model being correct. This model cannot generate the heterosexual epidemic, so no θ makes it match reality; a posterior would concentrate on whichever parameter set least-badly compromises across conflicting targets and report that with false confidence. "These parameter sets are not ruled out, given stated uncertainty" stays true under misspecification.

**The structural change that matters most.** Four overlapping mechanisms currently do the accept/reject/rank job — `calib_distance.py` (a weighted sum), `calib_acceptance_gates.py`, `calib_historical_shape_gates.py`, `calib_non_msm_diagnosis.py`. All four collapse into **one** implausibility number, and the combination rule changes from a **weighted sum** to a **maximum**:

> Under the current weighted sum, an excellent MSM fit can **compensate** for a catastrophic non-MSM failure. That is how the submitted base case scored acceptably while producing literally zero non-MSM diagnoses. Under history matching, a parameter set is ruled out if it fails badly on **any** target. No compensation.

---

## 2. The method

### 2.1 Implausibility

For each target `i` at each candidate parameter vector `x`:

```
I_i(x) = | E[f_i(x)] − z_i | / sqrt( V_obs,i + V_code,i + V_ensemble,i + V_disc,i )
```

- `E[f_i(x)]` — simulator output for target `i` at `x`
- `z_i` — the observed (or estimated) target value
- denominator — the **total uncertainty budget** for that target

### 2.2 Combination across targets

```
I_max(x)  = max_i  I_i(x)          ← primary rule
I_(2)(x)  = second largest I_i(x)  ← robustness to one badly-specified target
I_(3)(x)  = third largest I_i(x)   ← report only
```

**Primary rule is `I_max`.** Report NROY size under all three. Use `I_(2)` only if a specific target is demonstrably misspecified *and* that misspecification is documented — never to enlarge NROY.

### 2.3 Cutoff

```
NROY = { x : I_max(x) <= 3 }
```

Justification: **Pukelsheim's 3-sigma rule** — any continuous unimodal distribution holds at least **95.06%** of its mass within 3 standard deviations of its mean. The threshold is therefore distribution-free rather than chosen. **Do not change it.** If NROY is too small, that is a finding about the model, not a reason to move the cutoff.

> **CORRECTED 2026-09-10.** This section previously said "at least 93%". That figure is wrong and it was
> manuscript-bound. Verified at source — Pukelsheim F (1994), *The Three Sigma Rule*, The American
> Statistician 48(2):88–91: "the probability for X falling away from its mean by more than 3 SD's is at
> most 5%, Pr(|X − μ| ≥ 3σ) ≤ 4/81 < 0.05" (Vysochanskii–Petunin), i.e. **≥ 95.06%** for any unimodal
> distribution. The distribution-free Bienaymé–Chebyshev bound at 3σ is ≥ 88.9%. Neither is 93%.
> Andrianakis et al. state 95%. Asserted in `tests/test_hm_implausibility.py::test_cutoff_and_pukelsheim_coverage_figures`.
> **Fix this wherever it has reached manuscript text.**

> **A second presentational note.** Andrianakis et al.'s equation (8) is written *squared* —
> `I_j(x) = [E − z]² / (V_obs + V_code + V_ensemble + V_disc)` — while §2.1 above and the standard
> presentation use `|E − z| / sqrt(V)`. The square-root form is the one that makes `I ≤ 3` a *number of
> standard deviations*, which is what Pukelsheim bounds. They rule out identical sets provided the cutoff
> is squared or unsquared consistently. This build uses the square-root form throughout. Worth a footnote
> if the manuscript cites equation (8) by number.

---

## 3. The variance budget — where judgement lives

This is the heart of the method. Each term must be **derived and documented**, never chosen to produce an outcome.

### 3.1 `V_obs` — observation uncertainty

| Target class | Derivation |
|---|---|
| **UNAIDS / CSAVR series** (PLHIV, new_infections, ART) | The workbook carries `(All Age Low)` / `(All Age Mid)` / `(All Age High)` columns in `../data/raw/validation/DiseaseData.xlsx`. Treat Low/High as a 95% interval: `sigma = (High − Low) / 3.92`. Verify that reading against the UNAIDS methods annex before relying on it; if the bounds are not 95%, correct the divisor and say so. |
| **SHM diagnosis counts** | Sheet `Stiching 1.1 (Diagnoses)` carries both `reported` and `adjusted` columns from 2017 onward (e.g. 2024 MSM: reported 267, adjusted 301). **That gap is an empirical handle on reporting-delay/ascertainment uncertainty** — use it to derive `sigma` rather than assuming a value. For years with no `adjusted` column, state how the derived relationship is extrapolated and label it a *derived estimate*. |
| **ECDC case reports** | Surveillance counts. Derive ascertainment uncertainty from the source's own documentation; if none exists, state that and use the SHM-derived relationship as a proxy, labelled as such. |

### 3.2 `V_code` — emulator variance

**Zero. Do not build an emulator.**

Andrianakis et al. use Gaussian process emulators because their simulator is too expensive to evaluate at every candidate. This simulator runs in ~60 s with 14 workers available, so a 4,000-point wave is roughly 5 hours of wall clock. Running directly removes `V_code` from the denominator and removes emulator construction, validation and diagnostics from the workload entirely. **If someone proposes building an emulator, the answer is no unless wall-clock becomes the binding constraint and that is demonstrated with timings.**

### 3.3 `V_ensemble` — simulator stochasticity

The convention (Andrianakis §"Ensemble Variability") is the **mean or the 95th percentile of sample variances** across repeat stochastic runs. Record both and state which is used.

- Source: task **A-5** re-runs seed variability at n = 40–50 on post-fix code. Use that.
- ~~The n=15 figures in `outputs/hta_results/probe_diagnostics_20260906/raw/seed_variability_summary.csv` are **pre-fix** ... Usable for the prototype only.~~

> **CORRECTED 2026-09-10.** That file is **not usable even for the prototype.** It holds QALY, cost and
> infections-averted *economic outcomes* — it carries no epidemiological series at all (no PLHIV, no
> diagnoses, no AIDS deaths, no ART stock), so it cannot supply `V_ensemble` for a single calibration
> target. `V_ensemble` is therefore **0.0 with status `underivable` for every target** until A-5 lands.
> Combined with `V_obs` = 0 for every tier-1 target (§4 note below), this means **all 92 tier-1 variance
> budgets are 100% `V_disc`**: the tier-1 acceptance rule currently reduces to "within 3 × √(0.1 × the
> ensemble's own variance at that year)", which is a statement about ensemble spread, not about agreement
> with data. **A-5 is now the cheapest thing that makes the calibration mean something.**
- **CURRENT 2026-09-15:** calibration learning waves run **stochastically**, averaging three
  replicate seeds per vector. The same seeds are used at every vector (CRN), and A-5's independently
  measured per-run `V_ensemble` is divided by the number of replicates. This supersedes the earlier
  deterministic-mode decision: subsequent audits showed that deterministic allocation introduces
  material bias rather than merely removing noise.

### 3.4 `V_disc` — model discrepancy

**This is where judgement relocates, and the whole methodological argument depends on it being declared rather than hidden.**

- Andrianakis et al. use **10% of the variance of simulator output training data at each wave**, stated openly as an illustrative choice. Start there.
- **CSAVR-derived targets need an additional discrepancy term** beyond their published interval, because comparing this model to Spectrum's back-calculation is a model-to-model comparison, not a model-to-data comparison. Specify and justify that increment explicitly.
- **Required output: the ensemble-size-versus-`V_disc` curve.** Plot NROY size as a function of the discrepancy multiplier across at least an order of magnitude. **That curve is the evidence the tolerance was not tuned.** A build that reports an NROY size without that curve is incomplete.

> **Produced 2026-09-10, and it is steep.** Post-C-1 target set, 4,000 pre-fix particles: NROY = 0 / 0 / 0 / 1 / 42 / 496 / 3,991
> at multipliers 0.01 / 0.0316 / **0.10** / 0.316 / 1.0 / 3.16 / 10.0. A flat curve would say the NROY set is determined by the
> data; this one says it is determined by the discrepancy choice. **That is §10 escalation condition 2, fired.**
> The *pre*-C-1 curve, by contrast, is flat at zero across the whole range — which is what tells you the non-MSM failure is
> structural rather than a tolerance artefact.

> **A structural objection to `V_disc` as specified, raised per handoff §1A.6 and not acted on unilaterally.**
> Making the denominator a fraction of the *ensemble's* output variance makes it proportional to how uncertain
> the ensemble happens to be at that year, not to how well the model can be expected to represent reality there.
> On the prototype: `ecdc_hiv_confirmed_cases@1995` has total σ = 25.4 against z = 584 (a ~4% tolerance) while
> `shm_new_diagnoses_msm@1996` has total σ = 19,591 against z = 370 (~5,300%). Same nominal 10%. Years where the
> model is wildly uncertain become almost unconstraining; years where it happens to be tightly clustered become
> brutally constraining, and which is which is a property of the design, not of the epidemiology. Faithful to the
> reference, but it deserves a manuscript sentence and possibly a different specification — **decision D-12**.

---

## 4. Target register (B-4)

> **BUILT 2026-09-10.** `configs/hm_target_register.json` (declarative) + `hm_target_register.py` (resolver):
> 11 series → 319 scalar (series, year) targets. Rendered tables:
> `outputs/calibration/hm_prototype_20260910/raw/target_register_scalar.csv` and `…_series.csv`.
>
> **Provenance checks this section asked for, done:** UNAIDS low/high **are** 95% intervals (Methods annex
> JC3153_GAU25 Annex 1, verbatim: the bounds "define the range within which the true value lies in 95% of
> cases"), so `/3.92` stands. The AIDS-death provenance check in §4.1 was run: this workbook holds the
> **modelled** UNAIDS series (Spectrum low/mid/high plus separate `Model Low/Mid/High` columns), not the raw
> CBS vital-registration series — so tier 2 stands, and a raw CBS series would be a *new* tier-1 target rather
> than a substitute.
>
> **`V_obs` is underivable for five series** (§10 escalation 3, fired): the SHM reported/adjusted pair measures
> **notification delay only** — verified verbatim in the H-TEAM cohort paper, "Numbers were adjusted for the
> delay in notification to Stichting HIV Monitoring" — and behaves exactly as §3.1 hoped, σ decaying with lag
> (2024: 73, 2023: 34, 2022: 18, 2021: 10, 2020: 5, 2019: 3, 2018 and 2017: 0). But **23 of the 29 years sit at
> lag ≥ 7 where the observed correction is exactly zero**, and under-ascertainment — people never tested, or
> never entered ATHENA-reporting care — appears in *neither* column, so the workbook contains no handle on it at
> all. ECDC publishes no uncertainty, and the §3.1 fallback of proxying from SHM is useless here because every
> ECDC year is at lag ≥ 9. `UNAIDS-ART` has no low/high columns at all. **Decision D-13.**
>
> **Not in the register, and a blocker for HM-5:** the four RIVM PrEP-programme series. They constrain six of the
> 27 parameters in `calib_space.json` (`prep_oral_init_2019..2025`, `prep_oral_dropout_*`), so those six are
> currently unconstrained by any wave. The `PrEP-Historical` sheet is an analyst-built mapping layer
> (`target_status = mapped_base`), publishes no uncertainty, and its 2025 row is an H1 partial-year check.
> Deciding their tier requires reading the RIVM 2025 PrEP evaluatierapport.
>
> **`ecdc_hiv_confirmed_cases` duplicates `shm_new_diagnoses_total` for 1996–2017** — two targets for one
> quantity double its exclusion pressure under a maximum rule, which is weighting smuggled back in through target
> selection. The register defaults to restricting it to 1990–1995, where SHM publishes only a `<=1995` lump.
> **Decision D-11.**

Produce one table, one row per target, with these columns:

`series_name | source_file | source_organisation | year_coverage | epistemic_tier | role | z_i derivation | V_obs derivation | V_disc derivation | notes`

### 4.1 Tiering rule — apply uniformly

- **Tier 1 — observed counts → calibration targets, tight tolerance.** SHM diagnoses (total, MSM), ECDC AIDS and HIV confirmed case reports.
- **Tier 2 — model-derived estimates → cross-validation comparators, wide tolerance, not calibration targets.** UNAIDS PLHIV, `new_infections`, ART. These are Spectrum CSAVR outputs: a Bayesian back-calculation fitted to the same case-surveillance diagnoses and vital-registration deaths already used in Tier 1. Weighting them as independent targets double-counts the underlying data.
- **Out of calibration entirely.** `new_diagnoses_non_msm` — becomes an exogenous observed input under C-1, so it is no longer a target the model is fitted against.

**Check each series against its actual provenance rather than assuming from this list.** UNAIDS AIDS-deaths in particular: vital-registration HIV deaths are an *input* to CSAVR, so a vital-registration-derived death series may be Tier 1 while the UNAIDS modelled AIDS-death series is Tier 2. Establish which the workbook actually holds.

### 4.2 Required disclosure

The tiering must be **justified by source epistemics, applied uniformly, and adopted with an explicit written statement that it was decided after the gate failures were already known.**

> **Demonstrate that the rule also reclassifies targets the current gates are passing.** That demonstration is what shows the rule is principled rather than self-serving. A tiering that only ever loosens the constraints you were failing is gate-shopping, and a methods reviewer will read it that way.

---

## 5. Wave loop (B-6)

```
wave 1:  space-filling design (Latin hypercube) over the full prior box
         → evaluate simulator → compute I_max → NROY_1 = {I_max <= 3}
wave k:  Latin hypercube inside NROY_(k-1), optionally adding targets
         → evaluate → NROY_k
stop:    when NROY volume stops shrinking materially, or targets are exhausted
```

- **Pre-specify the stopping rule before running.** State the volume-change threshold and the maximum wave count in writing first.
- **Report NROY volume per wave** as a fraction of the prior box.
- Adding targets progressively across waves is standard practice and is allowed — but the target set for each wave must be declared **before** that wave runs.
- **Build on the LHS infrastructure, not the ABC-SMC runner.** `run_lhs_calibration.py` and `run_lhs_pipeline.py` already do design generation, parallel dispatch and output writing. Wave 1 *is* an LHS plus a filter.

### 5.1 Continuing a completed run

Use `run_history_matching.py --continue-from PARENT_RUN` to begin at the next declared wave without
replaying earlier simulations. Continuation verifies the parent manifest and every recorded artefact,
requires identical scientific/scoring inputs and compatible runtime modules, validates the final primary
NROY rows and cutoff, and writes into a new output directory. The new design is drawn from the prior
NROY bounding box and thinned by proximity to its observed point cloud. The parent remains read-only and
the new manifest records the provenance chain.

`design_id` is a row identifier local to a wave. It identifies one **complete joint parameter vector**;
it is not a stochastic seed and must always be interpreted with the source run and wave.

### 5.2 Confirming sparsely selected vectors

`run_hm_confirmation.py` re-evaluates explicitly selected NROY vectors under many fresh seeds. This tests
whether a vector selected from a three-replicate mean remains plausible when its expected trajectory is
estimated more precisely. It is a stability analysis, not another wave: it does not update NROY geometry
and does not assign posterior weights. The output includes seed-level results, confirmed means, empirical
standard errors, original-versus-confirmed shifts, target-level implausibility and a checksummed manifest.

---

## 6. Code plan

### 6.1 Reuse as-is

- Simulator wrapper and config patching
- `calib_target_loader.py` (path resolver fixed 2026-08-22)
- Summary-statistic extraction (`calib_extractor.py`, `calib_snpe_summary_stats.py` patterns)
- Parallel execution harness from `run_lhs_pipeline.py`
- Output writers and manifest machinery

### 6.2 Build new

The core modules below were built on 2026-09-10 and subsequently extended with stochastic replicate
averaging, measured `V_ensemble`, strict run contracts, held-out exports, checksum-verified continuation,
and `run_hm_confirmation.py`. As of 2026-09-15, the focused continuation and confirmation paths are covered
by 115 history-matching tests across `tests/test_hm_implausibility.py`,
`tests/test_hm_variance_budget.py`, `tests/test_hm_target_register.py`,
`tests/test_hm_wave_loop.py`, and `tests/test_hm_confirmation.py`.

**§6.3 "Retire" has NOT been done.** `calib_distance.py`, `calib_acceptance_gates.py`,
`calib_historical_shape_gates.py`, `calib_non_msm_diagnosis.py` and the ABC-SMC runner are all untouched and
still live — the new machinery sits alongside them rather than replacing them. Retiring them is task **B-5** and
should not happen until a production wave has actually run, so that the old path stays available for comparison.

| Module | Responsibility |
|---|---|
| `hm_target_register.py` | Loads the §4 register; resolves `z_i`, `V_obs`, `V_disc` per target per year |
| `hm_implausibility.py` | Computes `I_i(x)`, `I_max`, `I_(2)`, `I_(3)`; pure functions, fully unit-testable |
| `hm_variance_budget.py` | Assembles the four variance terms; emits the per-target budget as a reportable table |
| `run_history_matching.py` | Wave loop: design → dispatch → score → NROY → next design |
| `run_hm_confirmation.py` | Many-seed stability check for selected complete parameter vectors; no NROY update |
| `hm_diagnostics.py` | NROY volume per wave, ensemble-size-vs-`V_disc` curve, per-target implausibility distributions, which target is binding where |

### 6.3 Retire

- `calib_distance.py` `component_weights` (the weighted sum)
- ABC-SMC tolerance schedule and adaptive perturbation kernel in `run_abc_smc_calibration.py`
- Toni et al. importance weights (UNC-02A)
- The four separate gate modules' accept/reject roles — `calib_acceptance_gates.py`, `calib_historical_shape_gates.py`, `calib_non_msm_diagnosis.py`
- **Cut SNPE from the manuscript entirely.** Rounds still yield zero posterior samples after three real bug fixes.

**Do not delete the ABC-SMC code.** Mark it inactive and record in the checklist that UNC-02A's toy-posterior validation proved the implementation correct — which is *why* the failure to accept particles is attributable to the model rather than the inference code. That is a result, not dead weight.

### 6.4 Thresholds to retire, with justification recorded

Each of these is either replaced by a derived implausibility or explicitly retained with a written justification for its specific numeric value:

`incidence_recent_ratio_ceiling = 1.75` · `plhiv_recent_mape_ceiling = 10.0` · `art_recent_mape_ceiling = 10.0` · `diagnosis_floor = 0.90` / `diagnosis_ceiling = 1.50` · `aids_peak_to_recent_mean_floor = 2.0` · `aids_bounds_fraction_floor = 0.40` · all `historical_shape_settings` MAPE ceilings

---

## 7. Build order and acceptance

| Stage | Deliverable | Acceptance |
|---|---|---|
| — | **STATUS 2026-09-15** | HM-1–HM-4 ✅ · two bounded post-fix stochastic learning waves complete · focused continuation ✅ · many-seed confirmation prepared · HM-6 not frozen |
| **HM-1** | Target register (§4) | Every target has a tier, a documented `z_i`, a derived `V_obs`, and a `V_disc` rationale. The uniformity demonstration (§4.2) is written. |
| **HM-2** | `hm_implausibility.py` + `hm_variance_budget.py` | Unit tests on synthetic targets with known implausibility, including a target whose budget is dominated by each of the four variance terms in turn. Max / 2nd-max / 3rd-max verified against hand-computed cases. |
| **HM-3** | Prototype rescoring (§9) | NROY size reported under all three combination rules; ensemble-size-vs-`V_disc` curve produced; scope limit stated. |
| **HM-4** | `run_history_matching.py` wave loop | Waves reproducible from a manifest; NROY volume per wave reported; stopping rule pre-specified in writing before the first production wave. |
| **HM-5** | Production waves | Post-fix code; canonical parameter space; C-1 landed; stochastic estimator and variance terms declared; per-wave target sets declared in advance. The 2026-09-15 runs are explicitly labelled bounded learning waves, not the final production ensemble. |
| **HM-6** | Frozen NROY ensemble (B-7) | Checksummed manifest; **complete parameter rows preserving joint dependence — never independent marginals**; provenance and implausibility values for every member. |

---

## 8. Guardrails — violations invalidate the build

1. **Do not tune `V_disc`, any variance term, or the cutoff to make NROY non-empty.** The entire methodological argument is that tolerances are *derived from uncertainty budgets* rather than *chosen to produce acceptance*. Widening the discrepancy term until something survives destroys the paper's central claim, and does so invisibly because the output looks better. If something must change for NROY to be non-empty: **stop, report what would need to change and by how much, report what that implies scientifically, and wait for a decision.**
2. **An empty or tiny NROY is a result, not a failure.** It says the model cannot reproduce Dutch surveillance data within a stated uncertainty budget. That is publishable. Report it immediately and stop — it changes the paper's framing and is Rishi's decision.
3. **Do not build an emulator** unless wall-clock is demonstrated to be binding, with timings.
4. **Do not build the wave loop on the ABC-SMC runner.** LHS is the right base.
5. **Never use the word "posterior" or "credible interval"** for any output of this method. The vocabulary is: non-implausible, NROY, plausible-fit ensemble, range across the ensemble.
6. **No pre-fix particles in any production NROY.** All existing particle CSVs were generated with the defective `demography.py`.
7. **Do not use `I_(2)` or `I_(3)` as the primary rule** to enlarge NROY. `I_max` is primary.
8. **Never invent a parameter value, a bound, or an uncertainty interval.** A missing value is a blocker to report, not a number to supply. This includes "plausible ranges" for parameters that have no empirical bounds — say there are none, as A-10 correctly did for the lenacapavir utility weights.

---

## 9. Prototype first (B-3) — no dependencies, do this early

Implement `hm_implausibility.py` and rescore existing particle CSVs. **Same simulator evaluations, new acceptance rule, no new compute.**

Sources:
- ~~`data/model-outputs/v11_outputs/.../abc_smc_particles_20260604_065622.csv` (20,000 rows)~~ — **CORRECTED 2026-09-10: UNUSABLE.** Two problems. The path is recorded relative to the project root, not the model directory (`../data/model-outputs/...` from `hiv-prep-model/`). More importantly, the file has **196 columns and zero per-year simulator output columns** — only parameters, `ll_*`, gate metrics and distances. There is nothing to compute an implausibility from. Recorded in `outputs/calibration/hm_prototype_20260910/raw/particle_sources.csv`.
- `outputs/calibration/abc_smc_v13_23_snpe_bootstrap/abc_smc_particles_20260822_205519.csv` (4,000 rows, 1,150 columns, 414 output-series columns) — **the only rescorable sweep.** Read "24,000 particles" below as **4,000**.
- **A third scope limit this section does not state:** those 4,000 particles are five ABC-SMC generations, *not* a space-filling design, so their spread reflects ABC's own selection pressure. `V_disc` is defined as a fraction of the *wave design's* output variance, so computing it from an ABC ensemble is an *approximation* of what the method specifies — a further reason not to read the NROY counts as estimates of anything.

**Scope limit — state this prominently in the prototype report.** These particles were generated with **pre-fix `demography.py`**, shown by A-11 to be materially defective. The prototype tests whether the implausibility machinery behaves sensibly. **No NROY set derived from these particles may be carried into HM-5, HM-6, or any result.**

**Expected finding:** non-MSM targets will rule out nearly everything under `I_max`. That is correct behaviour — it is the same fact the reachability audit found (`new_diagnoses_non_msm`: mean 0.03 against a target of 199, max |r| = 0.12 across all parameters). Report it. Do not tune around it. It also disappears legitimately once C-1 lands and non-MSM leaves the calibration target set.

> **CONFIRMED 2026-09-10, more strongly than "nearly everything".** `shm_new_diagnoses_non_msm@2018` is the
> binding target for **4,000 of 4,000** particles; per-year implausibilities on that series run into the hundreds
> (e.g. `@2011`: 551–615). And it still rules out the entire space at a `V_disc` multiplier of **10** — a
> hundred-fold increase on the Andrianakis value. Not a tolerance problem.
>
> **With non-MSM removed** (the post-C-1 target set), NROY is still **0 / 4,000** under `I_max` (2 under `I_(2)`,
> 22 under `I_(3)`), and the binding constraint moves to `ecdc_hiv_confirmed_cases@1995` — binding for 97.0% of
> particles, ruling out 99.9% on its own.
>
> **An unstated decision this exercise exposed, and the biggest single lever in the method.** Treating every
> (series, year) pair as a separate output makes `I_max` a maximum over ~92–121 numbers; Andrianakis et al. took a
> maximum over **18**, for a 22-parameter model. More outputs under a maximum rule is a strictly more stringent
> test and nothing compensates for it. Measured: all 92 targets → NROY **0**/4,000; the most recent five years
> (10 targets) → **2,272**/4,000; the latest year alone (2 targets) → **2,347**/4,000. This changes the answer
> more than any variance term does. **Decision D-10, and it must be declared before wave 1.**

---

## 10. Escalate immediately if

> **Status 2026-09-10: conditions 1, 2 and 3 all fired in the HM-3 dry-run.** (1) NROY is empty — 0/4,000 under
> `I_max` in both target sets; qualified by the pre-fix particles, though the *pre-C-1* zero is scientifically
> meaningful because it holds at every multiplier tested. (2) NROY is extremely sensitive to `V_disc` — 0 → 3,991
> over three orders of magnitude. (3) `V_obs` cannot be derived for five series. (4) Provenance was checked and
> the §4.1 tiering held. (5) No brief §12 path was reopened. Details and evidence paths:
> `outputs/calibration/hm_prototype_20260910/HM_BUILD_REPORT_2026-09-10.md` §9.
>
> **Nothing was tuned.** No variance term, threshold, bound or multiplier was moved to make anything pass. The
> 0.10 multiplier is Andrianakis's declared value, the cutoff is 3.0, and the one declared assumption in `V_obs`
> (the SHM delay-uncertainty fraction) was swept over {0, 0.25, 0.5, 1.0, 2.0} rather than fitted — NROY is 0 at
> every value, so it is demonstrably inert here.

- NROY is empty or contains fewer than ~10 members at `I_max <= 3` with a defensible `V_disc`.
- The ensemble-size-vs-`V_disc` curve shows NROY size is extremely sensitive to the discrepancy term (meaning the result is driven by that choice rather than by the data).
- A target's `V_obs` cannot be derived from any source and would have to be assumed.
- Any target's provenance turns out to be different from the §4.1 tiering (e.g. a series believed observed turns out to be modelled).
- The build appears to require reopening a path in brief §12.

---

## 11. Reporting

Per §1A of the handoff, plus specifically for this build:

- Per-target implausibility distributions across the wave — **which target is binding, and where**
- NROY volume per wave as a fraction of the prior box
- The ensemble-size-versus-`V_disc` curve (required)
- The full variance budget table: every target, every term, every derivation
- The written statement on tiering uniformity and post-hoc adoption (§4.2)
- A plain statement of what NROY means and what it does not: it is not a probability distribution, so it supports a **range** across the ensemble, not P(cost-effective)

### Downstream consequence to flag in the manuscript

NROY has no probability structure, so a conventional CEAC over the *epidemiological* parameters is not available. The accepted approach: keep the PSA **probabilistic over economic parameters** (costs, utilities, discount rates), and treat NROY members as a **scenario set** for epidemiological parameters. The CEAC is then conditional on epidemiological scenario rather than integrating over it. State this explicitly rather than letting a reviewer discover it.

---

## 11A. What is still open — as of 2026-09-10

Consolidated so it does not have to be reassembled from the annotations above.
Evidence for every figure: `outputs/calibration/hm_prototype_20260910/HM_BUILD_REPORT_2026-09-10.md`.

### Decisions only Rishi can make (none taken unilaterally)

| ID | Decision | Why it cannot wait |
|---|---|---|
| **D-10** | Scoring granularity: every (series, year) pair, anchor years, or a summary-statistic set | Changes the answer more than any variance term: 92 targets → NROY 0/4,000; 10 → 2,272; 2 → 2,347. A reviewer's first question will be "why 92?" |
| **D-11** | Role of `ecdc_hiv_confirmed_cases` — 1990–1995 only, full overlap, or cross-validation | Scoring it against 1996–2017 double-counts a quantity SHM already covers, reintroducing weighting through target selection |
| **D-12** | `V_disc` specification — ensemble-variance fraction vs relative to each target's own scale | The same nominal 10% is a 4% tolerance on one target and ~5,300% on another |
| **D-13** | Proceed with `V_obs` = 0 for tier 1, or find an ascertainment-uncertainty source first | Zero biases towards over-exclusion, so any NROY is a subset of the true one. Either choice is defensible; leaving it undecided is not |
| **D-07** | Tier-2 discrepancy increment multiplier (currently 1.0) | Inert under the primary rule — tier-2 targets never enter `I_max` — so it affects reporting only, but it should be a stated choice |
| — | Tier of the four RIVM PrEP-programme series | They are absent from the register, leaving six `prep_oral_*` parameters unconstrained by any wave. Needs the RIVM 2025 PrEP evaluatierapport read |

### Work still to do

| | Status |
|---|---|
| **A-5** — seed variability on post-fix code, n = 40–50, **for the calibration target series** | **Not started. Top blocker.** Without it `V_ensemble` = 0 everywhere and the entire tier-1 denominator is one declared multiplier times the ensemble's own spread |
| **B-1 / B-2** — canonical parameter space and parameter roles | Not started. The prior box *is* the space being excluded from |
| **C-1** — exogenous non-MSM forcing | Not started. Until it lands, one target rules out everything at every tolerance |
| **B-5 / §6.3** — retire `calib_distance` weights, the gate modules, the ABC-SMC schedule | **Not done, deliberately.** The old path stays live for comparison until a production wave has run |
| **HM-5** — production waves | Blocked by all of the above. `run_history_matching.py` refuses to run without `--acknowledge-prerequisites` |
| **HM-6** — frozen NROY ensemble | Blocked by HM-5 |
| Full test suite baseline | **Still not established.** The project `.venv` does not resolve inside the Cowork device VM, so the suite ran on the system `python3`, where 60 tests fail and 5 error — almost all because `torch`/`sbi` are missing or because some tests open `1980_test.json` at the repo root when it lives in `configs/`. Proven unrelated to this build by renaming every new file aside and re-running to byte-identical failure sets, but a real baseline needs a run under the project venv |

### Things to fix in text already written elsewhere

- The **93% Pukelsheim figure** (§2.3 here, and handoff B-3) is wrong — it is 95.06%. Check any manuscript draft that has already absorbed it.
- If the manuscript cites Andrianakis **equation (8)** by number, note that it is written squared while this build uses the square-root form (§2.3 note).
- The **downstream CEAC consequence** in §11 is unchanged and still needs stating in the manuscript: NROY has no probability structure, so the CEAC is conditional on epidemiological scenario rather than integrating over it.

---

## 12. Reading list

- **Andrianakis I, et al. (2015).** "Bayesian History Matching of Complex Infectious Disease Models Using Emulation: A Tutorial and a Case Study on HIV in Uganda." *PLoS Comput Biol* 11(1):e1003968. https://journals.plos.org/ploscompbiol/article?id=10.1371%2Fjournal.pcbi.1003968 — **the primary reference.** Implausibility formula, the four variance terms, the I > 3 cutoff and its Pukelsheim justification, how `V_disc` is specified in practice, wave workflow.
- **Andrianakis I, et al.** "History matching of a complex epidemiological model of HIV transmission by using variance emulation." *JRSS-C*. https://ncbi.nlm.nih.gov/pmc/articles/PMC5516248 — handling stochastic simulator variance specifically; directly relevant to `V_ensemble`.
- **Mahiane SG, et al. (2021).** "Updates to Spectrum's case surveillance and vital registration tool." *JIAS*. https://pmc.ncbi.nlm.nih.gov/articles/PMC8454676/ — the citation for §4.1's Tier 2 classification: what CSAVR is, that the Netherlands uses it, and that it is a Bayesian fit to case surveillance and vital registration rather than an independent estimate.
- **UNAIDS, "Methods for deriving UNAIDS HIV estimates" (Annex 1).** https://www.unaids.org/sites/default/files/2025-08/JC3153_GAU25_report_annex1_en.pdf — official methods statement; also the place to verify whether the Low/High bounds are 95% intervals before using the /3.92 divisor.
- **Craig PS, Goldstein M, Seheult AH, Smith JA** — foundational history-matching and implausibility work. Background only; Andrianakis is the applied entry point.
- **Kennedy MC, O'Hagan A (2001).** "Bayesian calibration of computer models." *JRSS-B* — the full-Bayesian alternative with explicit discrepancy modelling. Heavier than needed here, but the reference point for why history matching was preferred under known misspecification.
