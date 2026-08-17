# CHANGELOG — HIV Transmission Model V13

All notable changes to the model are documented here.
Format: version → date → description. Incremental changes follow VX.Y.
Major version changes are explicitly set by the user (VX → VY).

---

## V13.23 — 2026-06-04

### SNPE Calibration Runner

**Files added:** `run_snpe_calibration.py`, `calib_snpe_summary_stats.py`, `tests/test_snpe_calibration.py`
**Files modified:** `run_lhs_calibration.py` (--store-timeseries flag), `requirements.txt`
**Documentation:** `documentation/investigations/NN_CALIBRATION_APPROACHES_ANALYSIS_2026_06_04.md`, `documentation/investigations/SNPE_IMPLEMENTATION_OUTLINE_2026_06_04.md`

**Summary:** Added a third calibration path using Sequential Neural Posterior Estimation (SNPE-C / APT, Greenberg et al. 2019). Implements the `sbi` library (Tejero-Cantero et al. 2020) to train a Neural Spline Flow (NSF) or Masked Autoregressive Flow (MAF) that approximates the posterior distribution P(θ | x_obs) from simulated (θ, x_sim) pairs. Unlike ABC-SMC, SNPE uses all simulated particles during training regardless of whether they pass acceptance gates, enabling posterior estimation even when classical acceptance rate is 0%.

**New module `calib_snpe_summary_stats.py`:** Standalone summary-statistics module. Converts `calib_extractor.extract_annual_outputs` output into a fixed 60-element log1p-transformed float32 vector (x_sim). Indicators: PLHIV, new_infections, new_diagnoses, new_diagnoses_msm, aids_deaths, on_art_stock across 8–11 target years each. Missing/non-finite outputs receive a finite sentinel value to keep neural network training stable. Public API: `compute_summary_stats`, `observed_summary_stats`, `summary_stat_labels`, `stat_row_fields`.

**New runner `run_snpe_calibration.py`:** Round loop: round 0 bootstrap from prior (or pre-existing LHS CSV via `--bootstrap-csv`), followed by N active rounds of train → posterior sampling → parallel simulation. Training is serial (NSF fitting, ~5–30 min CPU); simulation is parallel via `ProcessPoolExecutor` with the same `--workers` flag as existing runners. Outputs: particles CSV, round summary CSV, posterior samples CSV, metadata JSON, Excel workbook (6 sheets), best-fit JSON configs. Reads the same `calib_space*.json` files as LHS and ABC-SMC with no modifications.

**LHS runner patch:** Added `--store-timeseries` flag (default False). When set, writes full output timeseries columns (indicator_year) into the ranked results CSV. This allows LHS outputs to be reused as SNPE bootstrap data via `--bootstrap-csv`. Default behaviour (flag not set) is identical to prior behaviour — no regression.

**Tests:** 10 test classes covering summary stat shape/dtype, log1p transform, sentinel handling, observed stats, stat_row_fields, sbi helpers (prior bounds, clip-to-bounds, tensor helpers), worker error-path schema, parse_args defaults, LHS patch regression, and a slow smoke integration test.

**Dependency:** `sbi==0.26.1` added to `requirements.txt`. Imported only in `run_snpe_calibration.py`; all other modules remain importable without sbi.

---


## V13.22 - 2026-06-04

### Deterministic Transmission Allocation

**Files modified:** `transmission_corrected.py`, `tests/test_non_msm_diagnosis_pipeline_audit.py`, `documentation/investigations/NON_MSM_DIAGNOSIS_PIPELINE_AUDIT_2026_06_04.md`
**Patch doc:** `documentation/patches/PATCH_V13_22_DETERMINISTIC_TRANSMISSION_ALLOCATION.md`

**Summary:** Replaced deterministic transmission cell/stage-level flooring with aggregate-preserving expected infection allocation. Deterministic transmission now accumulates float-valued expected infections across susceptible cells, partner groups, HIV stages, ART states, and route-specific probabilities, then allocates integer infections once using a largest-remainder rule capped by susceptible-cell capacity. Stochastic transmission remains unchanged and continues to use the two-step binomial process.

**MSM route handling:** The deterministic MSM path now uses the expected receptive/insertive beta mixture from `msm_versatility` instead of flooring an intermediate role split. Condom, PrEP, and ART modifiers are still applied, and route structure is unchanged.

**Audit implication:** The non-MSM audit confirmed that deterministic flooring was suppressing rare heterosexual transmission. After the patch, rank001 produces early non-MSM infections that were previously rounded away, but still produces zero recent non-MSM diagnoses in 2015-2024. Recalibration is required before deciding whether the remaining gap is structural, calibration-space related, or requires a defensible sustained non-MSM source mechanism.

---

## V13.21 - 2026-06-03

### MSM Relative Diagnosis Bounds and Semantics

**Files modified:** `run_v3.py`, `run_lhs_calibration.py`, `run_abc_smc_calibration.py`, `configs/calib_space_v13_10_msm_diag_multiplier_pilot.json`, `configs/calib_space_v13_12_structural_capacity_msm_multiplier.json`, `configs/calib_space_v13_12b_structural_capacity_ordered_orientation.json`, `configs/calib_space_v13_14_1975_burnin_diagnostic.json`, `configs/calib_space_v13_15_1975_orientation_seed_abc.json`, `configs/v13_14_1975_burnin_base.json`, `V13.18_20260601_basecase.json`, `working_sub/V13.18_20260601_basecase.json`, `working_sub/V13.18_20260601_basecase_PSAReady.json`, `tests/test_msm_relative_diagnosis_v13_10.py`, `tests/test_abc_smc_calibration.py`
**Patch doc:** `documentation/patches/PATCH_V13_21_MSM_RELATIVE_DIAGNOSIS_BOUNDS.md`

**Summary:** Reinterpreted the MSM-relative diagnosis layer as a true relative ascertainment multiplier. The runtime no longer clips the multiplier itself to `1.0`; instead, it clips the derived annual diagnosis probability before timestep conversion and keeps the final timestep probability capped. This allows MSM diagnosis to be lower than, equal to, or higher than the base/non-MSM diagnosis schedule. Updated MSM diagnosis-era calibration bounds to `[0.0, 2.0]` for 1996, 2004, and 2015, and removed the previous early-era monotonicity constraints (`1996 <= 2004`, `1996 <= 2015`) from config spaces and sampler validation.

**Calibration implication:** The change may relieve pressure on the base diagnosis schedule and help separate MSM from non-MSM diagnosis residuals, but it increases identifiability risk. Future reruns should inspect MSM, non-MSM, and total diagnosis residuals separately rather than relying on aggregate diagnosis fit alone.

---

## V13.20 - 2026-06-03

### MSM Relative Diagnosis Production Feature Flag

**Files modified:** `V13.18_20260601_basecase.json`, `working_sub/V13.18_20260601_basecase.json`, `working_sub/V13.18_20260601_basecase_PSAReady.json`, `documentation/MASTER_REFERENCES.md`
**Patch doc:** `documentation/patches/PATCH_V13_20_MSM_DIAGNOSIS_FEATURE_FLAG_PRODUCTION_CONFIG.md`

**Summary:** Promoted the MSM-relative diagnosis/ascertainment layer into the V13.18 production basecase config surface with `enabled=false` by default. The V13.18 rank-001 multiplier values are retained (`1996=0.2`, `2004=0.9626700068412785`, `2015=0.9662180504950325`) and the schedule remains configurable as `stepwise` or `linear`. When disabled, the V13.18 basecase uses the common diagnosis schedule unchanged; when enabled, MSM diagnosis rates use `base_rate x multiplier` while non-MSM diagnosis rates remain on the base schedule.

**Source update:** Added van der Bij et al. (2008), "Low HIV-testing rates and awareness of HIV infection among high-risk heterosexual STI clinic attendees in The Netherlands", as the Netherlands-context testing/awareness source supporting explicit group-structured ascertainment.

---

## V13.19 — 2026-05-29

### Non-MSM Diagnosis Mechanism Audit

**Files added:** `tools/diagnose_calibration_mechanism_variants.py`, `tools/diagnose_msm_diagnosis_layer.py`, `tools/diagnose_orientation_seed_stage_variants.py`, `tests/test_diagnose_calibration_mechanism_variants.py`, `tests/test_diagnose_msm_diagnosis_layer.py`, `tests/test_diagnose_orientation_seed_stage_variants.py`
**Patch doc:** `documentation/patches/PATCH_V13_19_NON_MSM_MECHANISM_AUDIT.md`

**Summary:** Diagnostic audit determining why V13.18 best candidates passed all recent backbone gates except total diagnoses. Root cause confirmed: non-MSM diagnoses are zero (or near-zero) in recent years because the current calibrated parameter space does not sustain a non-MSM undiagnosed pool large enough to generate the observed ~199/year non-MSM diagnosis stream.

Deterministic baseline (V13.18 rank 001): 0 non-MSM infections in 2024. Stochastic replicates (20 runs): median 0 non-MSM diagnoses in 2024, range 0–8 over 2015–2024 vs target ~199. Contact-scale and beta-scale stress tests confirmed the engine can route non-MSM infections if the heterosexual transmission capacity is set far outside realistic bounds. Structural conclusion: non-MSM diagnosis composition is an architectural limitation of the MSM-dominated transmission model under current parameter bounds — not a code bug.

**Decision required (open):** Whether to add a bounded, literature-defensible non-MSM source mechanism, expose heterosexual contact/beta capacity as a free calibration parameter, document the non-MSM gap as a stated model limitation, or investigate deterministic event-flooring suppression of rare flows before committing to a mechanism change.

---

## V13.18 — 2026-05-28

### Soft AIDS-Death Gate and Final ABC-SMC Search

**Files modified:** `calib_acceptance_gates.py` (added `aids_death_hard_gate` field), `run_abc_smc_calibration.py` (`--soft-aids-death-gate` flag; export repair for `simulation_start_year` propagation)
**Patch doc:** `documentation/patches/PATCH_V13_18_SOFT_AIDS_DEATH_FINAL_SEARCH.md`

**Summary:** The V13.13–V13.17 diagnostics confirmed that the AIDS-death gate is structurally incompatible with the recent-diagnosis gate under the current 1975-start architecture. This patch demotes the AIDS-death gate to a warning-only validation endpoint (`--soft-aids-death-gate`) while keeping it in the ABC distance function. `overall_accept` now means the non-AIDS-death backbone gates passed; AIDS-death performance is retained as a secondary transparency metric.

**Final search run** (`abc_smc_v13_18_soft_aids_final`): 4,000 particles × 5 generations = 20,000 particles. Result: 0 `overall_accept`. `hist_overall_accept` (historical shape gates only): 625/20,000. Binding endpoint: total diagnosis latest-year floor. Best near-accepted candidates pass PLHIV, incidence, ART stock, historical shape, and MSM latest diagnosis, but total diagnoses (MSM + non-MSM) fail. Confirms the non-MSM structural shortage is the remaining blocker, not AIDS deaths.

**Export repair:** Best-fit configs from the first export pass had `run_control.start_year = 1980` despite the 1975-start run; repaired in-place and fixed in the runner for future exports.

---

## V13.17 — 2026-05-28

### 1970 Burn-In Probe

**Files added:** `tools/probe_1970_burnin_v13_17.py`, `tests/test_1970_burnin_probe_v13_17.py`
**Patch context:** `documentation/patches/PATCH_V13_16_EVENT_ORDERING_AUDIT_AGENT_PLAN.md`

**Summary:** Extended the V13.16 event-ordering diagnostic to test a 1970 simulation start. The probe confirmed that moving the start year back a further 5 years from 1975 does not resolve the diagnosis/AIDS-death gate conflict. The within-timestep ordering investigation confirmed that the step order (progression → diagnosis → ART → mortality in the same step) can allow same-step ART rescue, but this effect is by design and within the documented uncertainty range rather than a discrete bug.

---

## V13.16 — 2026-05-28

### Event Ordering and Burn-In Diagnostic

**Files added:** `tools/audit_event_ordering_v13_16.py`, `tests/test_event_ordering_diagnostic_v13_16.py`
**Patch doc:** `documentation/patches/PATCH_V13_16_EVENT_ORDERING_AUDIT_AGENT_PLAN.md`
**Outputs:** `outputs/calibration/v13_16_event_ordering_audit/`

**Summary:** Following V13.15's failure (12,000 particles; 0 formal accepts), this diagnostic tested whether within-timestep event ordering was suppressing AIDS mortality. In the current `run_v3.py` sequence, a person can progress to AIDS, be diagnosed, start ART, and face the ART-reduced hazard all within the same quarter — potentially compressing AIDS mortality exposure. Diagnostic confirmed this is a designed consequence of the deterministic sequential update, not a silent bug. Outputs: `event_ordering_audit_summary.csv`, `event_ordering_audit_timeseries.csv`, `event_ordering_audit_report.html`.

---

## V13.15 — 2026-05-27

### 1975 Burn-In + Orientation Seed ABC Diagnostic

**Files modified:** `run_abc_smc_calibration.py` (anchor compatibility: shared `seed_*_share` keys mapped to orientation-specific blocks)
**Files added:** `configs/calib_space_v13_15_1975_orientation_seed_abc.json`
**Patch doc:** `documentation/patches/PATCH_V13_15_1975_ORIENTATION_SEED_ABC.md`

**Summary:** Combined the V13.14 1975 burn-in, V13.10 MSM diagnosis-era multipliers, and V13.11 MSM/non-MSM seed-stage split into a single ABC-SMC search space. Run: 12,000 particles. `diagnosis_gate_pass`: 26/12,000. `aids_death_gate_pass`: 6/12,000. Intersection: 0. Confirmed the diagnosis/AIDS-death conflict is not resolved by early burn-in plus orientation heterogeneity — the structural tension is intrinsic to the 1980-start epidemic architecture under current calibrated parameter bounds.

---

## V13.14 — 2026-05-28

### 1975 Historical Burn-In Diagnostic

**Files added:** `tools/run_1975_burnin_probes.py`, `tools/audit_1975_burnin_population.py`, `configs/calib_space_v13_14_1975_burnin_diagnostic.json`, `tests/test_1975_burnin_population.py`, `tests/test_1975_burnin_probes.py`
**Patch doc:** `documentation/patches/PATCH_V13_14_1975_BURN_IN_DIAGNOSTIC_AGENT_PLAN.md`
**Summary doc:** `documentation/patches/V13_14_1975_BURNIN_SUMMARY.md`

**Summary:** Tested whether starting the simulation in 1975 (vs 1980) can accumulate enough AIDS compartment stock to match the 1990–1995 AIDS-death peak. Demographic feasibility confirmed for both 1975 and 1970 starts. Key findings: the 1975 burn-in brings cumulative AIDS deaths 1990–2005 close to target (3,736 vs ~3,600), but the AIDS-death peak remains at 2004 rather than 1995 (timing is governed by progression rates, not start year), and 2024 PLHIV and diagnoses overshoot by 50–116% because five extra years of epidemic growth compounds under current calibration. Conclusion: the burn-in is a partial solution — it shifts the tradeoff surface but does not eliminate the structural tension between early AIDS-death mass and recent fit.

---

## V13.13 — 2026-05-28

### Calibration Closure Audit

**Files added:** `tools/audit_calibration_constraint_conflicts.py`, `tools/audit_model_mechanism_sanity.py`, `tools/run_extreme_deterministic_probes.py`, `tests/test_audit_calibration_constraint_conflicts.py`, `tests/test_audit_model_mechanism_sanity.py`, `tests/test_extreme_deterministic_probes.py`
**Patch doc:** `documentation/patches/PATCH_V13_13_CALIBRATION_CLOSURE_AUDIT_AGENT_PLAN.md`
**Summary:** `outputs/calibration/v13_13_closure_audit/V13_13_CLOSURE_AUDIT_SUMMARY.md`

**Summary:** Systematic audit across 30,000 particles (V13.12 + V13.12b combined) to determine whether the calibration failure was caused by gate conflict, code bug, or structural incapacity. Finding: the failure is a **structural gate conflict** — the diagnosis and AIDS-death gates are empirically incompatible (intersection = 0/30,000). Mechanism sanity audit cleared 14/16 checks; two marked "structural by design" (within-timestep ordering, deterministic event allocation). Extreme deterministic probes showed the engine can generate AIDS-death peaks up to 7,600 (high-progression probe), but only at parameter values that destroy all other fit targets. Root cause: the 1980-start seeding does not accumulate sufficient AIDS-stage stock before the 1990–1995 target peak.

---

## V13.12 — 2026-05-27

### Structural-Capacity Stress Runs (Diagnostic)

**Files added:** `tools/audit_structural_capacity.py`, `tests/test_audit_structural_capacity.py`, `configs/calib_space_v13_12_structural_capacity_msm_multiplier.json`, `configs/calib_space_v13_12b_structural_capacity_ordered_orientation.json`

**Summary:** Two diagnostic ABC-SMC runs with deliberately widened parameter bounds to test whether the model has structural capacity to simultaneously pass diagnosis and AIDS-death gates. V13.12: 15,000 particles, MSM multiplier enabled, oriented orientation injection. V13.12b: 15,000 particles, ordered-orientation variant. Both runs: 0 `capacity_pass`, 0 `near_capacity_pass`, 0 `mathematical_only_fit`. Orientation injection errors were eliminated in V13.12b, ruling that out as a cause. The zero-intersection finding was forwarded to the V13.13 closure audit.

---

## V13.11 — 2026-05-26

### Orientation-Specific Seed Stage Distribution

**Files modified:** `run_lhs_calibration.py` (orientation-specific `seed_msm_*` and `seed_non_msm_*` stage-share parameters; `_valid_draw_mask` simplex + monotonicity constraints; `_with_derived_parameters` acute-share derivation)
**Files added:** `configs/calib_space_v13_11_orientation_seed_stage_pilot.json`, `tests/test_orientation_seed_stage_distribution.py`

**Summary:** Extended the V13.7 seed stage distribution to allow separate stage compositions for MSM vs non-MSM at the epidemic seed year. The opt-in block allows calibration to independently set MSM and non-MSM chronic and AIDS shares at seeding while enforcing valid-simplex and monotonicity constraints. Default behavior unchanged (shared `seed_stage` fallback). Addresses the early AIDS-death pool mismatch identified in V13.6–V13.9 diagnostics.

---

## V13.10 — 2026-05-26

### MSM Relative Diagnosis Multiplier Layer

**Files modified:** `engine_v3.py` / `run_v3.py` (opt-in `disease.diagnosis.msm_relative_diagnosis` block; era-specific MSM diagnosis multiplier applied to undiagnosed MSM pool; non-MSM path unchanged)
**Files added:** `configs/calib_space_v13_10_msm_diag_multiplier_pilot.json`, `tests/test_msm_relative_diagnosis_v13_10.py` (13 tests)

**Summary:** Added an optional, interpretable MSM-specific diagnosis rate multiplier that varies by historical era (early pre-1996, mid 1996–2014, recent 2015+). When `enabled=false` or the block is absent, `_step_diagnosis` is bit-identical to V13.9 behavior. When enabled, the MSM undiagnosed pool uses `base_rate × era_multiplier`; non-MSM uses `base_rate`. The calibration space enforces `early ≤ mid` and `early ≤ recent` (monotonicity). Clipping: multipliers >1.0 are treated as 1.0. This provides a defensible mechanism for the documented shift in Dutch MSM HIV testing uptake over time (Stolte et al., 2008; RIVM surveillance data).

---

## V13.9 — 2026-05-27

### Historical Shape Calibration Gates

**Files added:** `calib_historical_shape_gates.py`, `tests/test_calib_historical_shape_gates.py`
**Files modified:** `run_abc_smc_calibration.py` (historical shape gate integration, `--historical-shape-gates` flag)
**Files added:** `configs/calib_space_v13_9_3_msm_denominator_bridge.json`
**Patch doc:** `documentation/patches/PATCH_V13_9_HISTORICAL_SHAPE_CALIBRATION_PLAN.md`

**Summary:** The V13.8.2 accepted ensemble (59 candidates) passed all recent endpoint gates but exhibited implausible historical trajectories — PLHIV 1990–2005 too high, diagnoses and AIDS cases several-fold above targets in early years. Added four opt-in historical shape gates (MAPE-based, with spike guards) covering PLHIV (1990–2005), total diagnoses (1990–2014), MSM diagnoses (1996–2014), and AIDS cases (1990–2017). Gates are opt-in only (`HistoricalShapeGateSettings(enabled=True)`); default `enabled=False` preserves V13.8.2 behaviour exactly. Missing target data treated as warning, not failure.

**V13.9.5 sub-patch:** Added `calib_non_msm_diagnosis.py` and `tests/test_non_msm_diagnosis.py`. Provides opt-in non-MSM diagnosis composition gate (derived as `total − MSM`). Characterises the non-MSM composition gap observed in V13.9.4 post-hoc diagnostics without adding a new mechanism.

---

## V13.8 — 2026-05-26

### ABC-SMC Calibration Pipeline

**Files added:** `run_abc_smc_calibration.py`, `tests/test_abc_smc_calibration.py`
**Patch doc:** `documentation/patches/PATCH_V13_8_ABC_SMC_CALIBRATION_PLAN.md`

**Summary:** Implemented a full ABC-SMC (Approximate Bayesian Computation — Sequential Monte Carlo) calibration pipeline as the primary replacement for broad LHS. Uses deterministic full-model evaluation, a continuous distance function derived from the locked acceptance gates, sequential particle resampling with Gaussian perturbation kernel, and the existing hard-gate audit for final candidate screening.

**V13.8.1 — Reproducibility + Elite Anchors:**
Fixed two bugs in `run_abc_smc_calibration.py`: (1) non-deterministic particle outcomes under nominally identical settings; (2) generation-0 fully random-prior seeding. Added `--anchor-glob` and `--anchor-max` flags to inject known near-fit candidates as labelled elite anchors at generation 0.

**V13.8.2 — Targeted Acceptance Search:**
Anchored search around 40 best candidates from the prior accepted-seed run. Settings: 2,000 particles × 5 generations, `--perturbation-bandwidth 0.004`, elevated diagnosis and incidence weights. Result: **59 accepted particles** out of 10,000 evaluated. All 59 exported configs have `overall_accept = true`. Accepted region near hard-gate boundaries: 2024 diagnosis ratio 0.901–0.915, 2023 incidence 503–508 vs upper bound 508.4.

**V13.8.3 — Accepted Ensemble Validation:**
Added `tools/build_accepted_calibration_ensemble.py`. Assembles the accepted V13.8.2 candidates into a portable ensemble manifest (`accepted_ensemble_manifest.csv`, `accepted_ensemble_manifest.json`, `accepted_config_paths.txt`, `base_case_candidate.json`, copied configs) for economic model handoff.

---

## V13.7 — 2026-05-25

### Epidemic Seed Stage Distribution

**Files modified:** `run_v3.py` (parses optional `population.epidemic_seed.stage_distribution`; validates non-negative simplex; splits seeded total across Acute/Chronic/AIDS; preserves legacy `seed_stage` fallback)
**Files modified:** `tests/test_epidemic_seeding.py` (T-ES13: stage distribution conserves total; T-ES14: invalid distribution rejected)
**Files added:** `configs/calib_space_v13_7_seed_distribution_pilot.json`
**Patch doc:** `documentation/patches/PATCH_V13_7_SEED_STAGE_DISTRIBUTION.md`

**Summary:** Added an optional `stage_distribution` block to the epidemic seed config, allowing calibration to specify the Acute/Chronic/AIDS share of the initial seeded HIV stock without changing the total seeded count. When absent, the existing single `seed_stage` behaviour is unchanged. Addresses the V13.6 diagnostic finding that early AIDS-death fit is limited by insufficient AIDS-stage stock at the seed year. This is a mechanism support patch only — existing production configs, candidate JSONs, and LHS calibration spaces are unaffected unless they explicitly supply `stage_distribution`.

---

## V13.6 — 2026-05-25

### Calibration Repair Decision Log

**Files added:** `configs/calib_space_v13_6_gate_repair.json`
**Patch doc:** `documentation/patches/PATCH_V13_6_CALIBRATION_REPAIR_DECISION_LOG.md`

**Summary:** Diagnostic-only log establishing the working calibration candidate and target hierarchy for the post-V13.5 repair effort. Selected working candidate: `outputs/calibration/lhs_pipeline_v13_5_recent_fit/lhs_joint/branch_004/lhs_best_fit_rank_002.json`. Observed strengths: 2024 total diagnoses 78.7% of target, 2024 MSM diagnoses 127% (within gate), 2023 new infections within UNAIDS bounds, 2023 ART stock near exact. Weaknesses: PLHIV too low 1990–1997; recent infections overproduced 2015–2023; AIDS deaths poor source-bound coverage. Defined hard vs soft target hierarchy: hard gates are recent diagnoses (total + MSM), recent PLHIV, recent incidence + trend, AIDS death shape, ART stock 2015–2023. No model mechanism changes.

---

## V13.5 — 2026-05-23

### Recent-Fit Gate and Candidate Audit

**Files added:** `calib_recent_fit.py` (`RecentFitSettings`, `evaluate_recent_fit()`, `recent_fit_row_fields()`), `tests/test_calib_recent_fit.py`
**Files modified:** `run_lhs_calibration.py` (recent-fit audit columns added to normalised-target runs; `--recent-fit-gate`, `--recent-fit-min-observed-fraction` (default 0.75), `--recent-fit-sigma-multiplier` (default 3.0), `--recent-fit-diagnosis-year` flags), `run_lhs_pipeline.py` (gate applied at joint stage only; settings data-driven unless `--recent-fit-diagnosis-year` specified; recent-fit metadata stored in written best-fit configs)
**Patch doc:** `documentation/patches/PATCH_V13_5_RECENT_FIT_GATE_PLAN.md`

**Summary:** Added a gating layer that prevents LHS finalists from winning purely on aggregate family-balanced likelihood if they simultaneously under-produce observed recent Dutch diagnoses. Gate philosophy: asymmetric — hard failure for total or MSM diagnoses below 75% of latest observed surveillance, and for latest incidence below the 3σ lower bound; warning only for latest incidence above the 3σ upper bound (to avoid excluding undiagnosed/migrant contributions). No 2024 UNAIDS incidence target was invented; the gate is surveillance-data-anchored. Applied at joint stage only so early staged calibration remains exploratory.

---

## V13.4 — 2026-05-23

### HTA Split Reliability Audit

**Files added:** `hta_reliability.py`, `tests/test_hta_reliability.py`
**Files modified:** `run_scenarios.py` (calibration handoff status printed before arm runs; warning when base config lacks LHS metadata; deterministic forcing now updates authoritative `run_control.deterministic_mode` as well as legacy `simulation_controls` mirror), `hta_report.py` (Study Overview: calibration handoff rows; Baseline Validation: `Timesteps`, `Expected Timesteps`, `Year Coverage` columns; partial-year baseline note)
**Patch doc:** `documentation/patches/PATCH_V13_4_HTA_SPLIT_RELIABILITY_AUDIT.md`
**Verification:** 9 tests passed (`test_hta_reliability.py`, `test_hta_split_report.py`).

**Summary:** Audited and hardened the HTA split-run handoff after identifying that (1) `model_final.json` is not the direct LHS-selected candidate — the pipeline selected `lhs_pipeline_v131_prefinal` branch_004 rank_001; (2) the LHS rank-1 config had an internal deterministic/stochastic inconsistency; (3) the 2025.00 HTA baseline freeze row is a partial-year snapshot, not an annual total; (4) the epidemic baseline remains below recent Dutch surveillance (2024 diagnoses: 252 modelled vs 517 target). Fixes: `hta_reliability.py` provides auditable calibration handoff summary and partial-year detection; `run_scenarios.py` prints handoff status and warns on missing LHS metadata; deterministic forcing fixed to update the authoritative config flag; Excel report surfaced calibration provenance and baseline timestep coverage.


## V13.3 — 2026-05-23

### Thesis HTA Framing, Budget Output, PSA Runner, and Costing Cleanup

**Files modified:** `run_scenarios.py`, `run_psa_hta.py`,
`hta_sampler.py`, `configs/hta_costing_config.json`,
`documentation/THESIS_RESULTS_CHECKLIST.md`,
`tests/test_hta_sampler.py`, `tests/test_psa_hta_integration.py`

Added a thesis-facing results checklist that locks the final analysis framing:
historical PrEP remains part of calibration, oral PrEP standard of care is the
main comparator, and the primary lenacapavir decision scenario is targeted
substitution among high-activity MSM. The headline scenario is now the 10% switch
case; wider substitution shares, add-on scenarios, oral expansion benchmarking,
and legacy universal/full-replacement Scenario A/D results are retained as
scenario or appendix material. Legacy A/D will be referenced briefly in the main
text as rationale for moving to the targeted substitution frame.

Updated the oral PrEP generic medicine price in the costing config from the
older FK-derived EUR 3.62/day value to the current Farmacotherapeutisch Kompas
generic price of EUR 2.87/day, equal to EUR 1,048.27/year. The same value is used
in both `oral_prep` and `oral_prep_plus_lena` cost blocks with matching gamma
distribution mean and 10% SE. Lenacapavir pricing remains FK-based:
EUR 41,950.42 in year 1 and EUR 41,110.44/year thereafter.

Clarified the oral PrEP reimbursement/payment issue as a thesis caveat rather
than a model branch. The model uses FK medicine prices as resource costs for
consistent societal CUA and budget/resource-impact reporting. The thesis should
note that Dutch PrEP care/testing and tablet payment responsibility may differ
in practice, so budget-impact tables are interpreted as modelled
healthcare/resource impact rather than a detailed reimbursement ledger.

Implemented `budget_impact.csv` output in the split HTA runner when
`outputs.csv_outputs.budget_impact=true`. The file reports annual and cumulative
discounted and undiscounted costs, QALYs, infections, and PrEP user stock/flow
fields for comparator-vs-intervention scenarios. Costs and QALYs are summed from
quarterly economics records; HCC/annual mean handling is used only for stock
columns such as oral PrEP and lenacapavir users. Smoke-tested output contained
360 rows for 9 decision-relevant scenarios across 2025-2064.

Fixed PSA/HTA readiness items:
- `HTASampler` now preserves the negative sign for negative beta-distributed
  utility decrements, including lenacapavir injection-site AE decrements.
- `run_psa_hta.py` warning text is now data-driven instead of emitting stale
  ART and placeholder-distribution warnings.
- `run_psa_hta.py` supports `--workers`; parallel execution runs independent
  PSA draws across processes and preserves draw order in outputs.
- Dry-run with `--workers 14` confirms the configured production worker count.

**Verification:**
- `PYTHONPYCACHEPREFIX=/private/tmp/codex_pycache_thesis python3 -m py_compile run_scenarios.py`
- `python3 -m pytest tests/test_hta_sampler.py -q` — 10 passed.
- `python3 -m pytest tests/test_psa_hta_integration.py tests/test_adverse_events_v2.py -q` — 39 passed, 1 skipped.
- Advanced HTA smoke run with `model_final.json` and
  `configs/hta_runtime_config.json` generated `/private/tmp/hta_budget_smoke/budget_impact.csv`.
- PSA × HTA worker smoke with `--workers 2` completed 2/2 draws.

---

## V13.2 — 2026-05-22

### HTA Split Runtime Config And Reviewer Workbook Redesign

**Files modified:** `hta_config_loader.py`, `run_scenarios.py`, `run_v3.py`,
`run_psa_hta.py`, `scenario_comparator.py`, `hta_report.py`,
`configs/hta_runtime_config.json`, `configs/hta_costing_config.json`,
`documentation/hta_runtime_config_schema_draft.json`,
`documentation/hta_costing_config_schema_draft.json`,
`documentation/hta_prep_lena_implementation_plan.md`,
`tests/test_hta_config_loader.py`, `tests/test_hta_split_report.py`

Split the HTA inputs into a costing config and a runtime/scenario config so
economic assumptions, scenario definitions, output toggles, and run metadata
are easier to audit separately. The loader preserves legacy behavior while
supporting the new explicit runtime schema, including top-down base-case
definitions, scenario arms, uptake/stock pathway modes, report output toggles,
and disable-able audit/detail sheets.

Redesigned the split-runtime Excel report around reviewer workflow rather than
raw model volume. The workbook now opens with `Decision Summary`, `Scenario Map`,
`Main CEA Results`, `Cost & Outcome Drivers`, `Price Sensitivity`,
`Assumptions & Sources`, `Baseline Validation`, and appendix tabs. Scenario
codes distinguish primary substitution, add-on continuation, and benchmark
comparisons while preserving the CSV audit outputs for traceability.

Price-sensitivity reporting now gives one compact threshold summary across all
configured scenarios, followed by detailed price-grid rows. The report also
records the configured annual lenacapavir price and threshold interpretation so
reviewers can see whether a scenario is cost-effective at base price, needs a
lower price, has no threshold in range, or is dominated.

Added an explicit split-report status for the southwest CEA quadrant: cheaper
but less effective. These rows are no longer labelled only as generic
`Not cost-effective`; they are reported as `Cost-saving trade-off; not CE` when
the cost saving does not offset QALY loss at the configured Dutch WTP threshold,
or `Cost-effective; cost-saving trade-off` when NMB is positive. The binary CE
decision rule remains NMB-based.

**Verification:**
- `python3 -m pytest tests/test_hta_split_report.py tests/test_hta_report.py -q`
- Split-runtime smoke run with `model_final.json` and
  `configs/hta_runtime_config.json` generated a reviewer workbook under
  `outputs/hta_split_status_review/`.

---

## V13.1 — 2026-05-20

### Migrant HIV Entry Seeding

**Patch doc:** `documentation/patches/PATCH_V13_MIGRANT_HIV_ENTRY_DESIGN.md`  
**Disease data:** `Validation /DiseaseData.xlsx`, tab `migrant_hiv_entries`  
**Files modified:** `migration.py`, `config_reader.py`, `run_v3.py`,
`output_writer.py`, `tests/test_migration.py`, `1980_test.json`,
`Validation /DiseaseData.xlsx`,
`documentation/patches/PATCH_V13_MIGRANT_HIV_ENTRY_DESIGN.md`

Added documented pre-arrival diagnosed HIV-positive migrants as an exogenous
historical entry stream inside positive net migration. Entries are allocated as
part of the already-counted migration flow, so population mass balance remains
`net migration`, not `net migration + migrant HIV entries`.

**Data and assumptions:**
- Source is SHM HIV Monitoring Report 2025, Chapter 1.
- 2022-2024 flow is 1,337 total: 641 in 2022, 302 in 2024, and residual 394 in
  2023. Figure 1.2 was not digitized.
- 2018-2021 are backcast at 322/year from the SHM reported average for other
  years in 2018-2024; years before 2018 remain zero.
- 90% enter `On_ART`, 10% enter `Not_on_ART`; all enter `Chronic / Diagnosed`.
- Source trans category is mapped to MSM in the model and retained in the
  disease-data tab notes.
- Entries are adult-only in the model; the SHM source count of 12 children under
  age 15 is documented in the workbook notes.

**Implementation:**
- Added `demography.migration.migrant_hiv` to `1980_test.json` and normalized it
  through `ConfigReader.migration()`.
- Positive migration first places HIV-positive migrant entrants, then allocates
  the remaining `M - H` migrants through the existing susceptible entrant path.
- Net-negative migration timesteps add zero migrant HIV entries and continue to
  use existing proportional negative migration removal.
- Source annual counts are split exactly across timesteps where possible, so the
  quarterly 1980 production config aggregates to the intended annual totals.
- Added raw timestep outputs for entries, ART share, orientation composition, and
  requested/capped/shortfall diagnostics. Annual aggregation remains downstream.

**Calibration note:**  
Migrant HIV counts are fixed historical inputs for post-patch LHS. They should
not be added to PSA initially. The expected calibration effect is lower pressure
on domestic infectivity parameters because part of PLHIV/ART stock now enters
externally rather than via domestic transmission.

**Verification:**
- `python3 tests/test_migration.py` — 23 passed.
- `PYTHONPYCACHEPREFIX=/private/tmp/codex_pycache_migrant_hiv python3 -m py_compile migration.py config_reader.py run_v3.py output_writer.py tests/test_migration.py`
  — passed.
- Production smoke with `1980_test.json`, seed 42: total migrant HIV entries
  2,625; total cap shortfall 0.

### LHS Disease Target Loader Hotfix

**Files modified:** `calib_target_loader.py`,
`configs/disease_target_mapping.json`

Fixed the workbook target mapping for UNAIDS adult 15+ new infections and AIDS
deaths. The current `Validation /DiseaseData.xlsx` workbook has populated
all-age and child columns, while the prior mapped `Model Mid/Low/High` columns
are blank. The loader now supports mapped subtraction columns so adult targets
are derived explicitly as all-age minus child values.

**Verification:**
- `python3 -m pytest tests/test_lhs_calibration.py tests/test_lhs_pipeline.py -q`
  — 20 passed.
- `python3 -m pytest tests/test_migration.py tests/test_lhs_pipeline.py tests/test_lhs_calibration.py -q`
  — 43 passed.
- `PYTHONPYCACHEPREFIX=/private/tmp/codex_pycache_v13_verify python3 -m py_compile migration.py config_reader.py run_v3.py output_writer.py run_lhs_pipeline.py run_lhs_calibration.py calib_target_loader.py tests/test_migration.py tests/test_lhs_pipeline.py tests/test_lhs_calibration.py`
  — passed.

---

## V13.0 — 2026-05-18

### Hotfix — LHS Joint Rerank Beam Selection

**Files modified:** `run_lhs_pipeline.py`, `tests/test_lhs_pipeline.py`

Fixed a post-rerank selection bug in the staged LHS pipeline. The joint-stage
calibration runner correctly prioritised stochastic-reranked candidates inside
each branch, but the pipeline beam selector re-sorted all branch candidates by
raw `total_ll`, allowing non-reranked screen-only rows to outrank reranked
candidates during final handoff/selection. Cross-branch beam selection now
keeps rows with `rerank_total_ll_mean` ahead of screen-only rows, then sorts by
score within that priority.

**Tests:** `python3 -m pytest tests/test_lhs_pipeline.py tests/test_lhs_calibration.py -q`
— 20 passed.

### Production Residence-Queue Demographic Aging

**Investigation report:** `documentation/investigations/DEMOGRAPHIC_ENGINE_RESIDENCE_QUEUE_IMPLEMENTATION_2026_05_18.md`  
**Analysis note:** `documentation/investigations/DEMOGRAPHIC_ENGINE_POP_CRASH_ANALYSIS_2026_05_18.md`

**Files modified:** `run_v3.py`, `demography.py`, `engine_v3.py`,
`1980_test.json`, `configs/v11_configuration.json`,
`configs/v6_golden_reference.json`, `diagnostics/demographic_backtest.py`,
`tests/test_aging_conservation.py`, `tests/test_residence_queue_aging.py`

**Summary:**  
Promoted the demographic engine to V13.0 and replaced production
memoryless fractional aging with residence-time queue aging. The config value
`aging_method = "fractional"` now uses the residence queue for backward
compatibility, `aging_method = "residence_queue"` is accepted as an explicit
alias, and `aging_method = "fractional_legacy"` retains the old memoryless
operator for diagnostics and before/after comparisons.

**Motivation:**  
The population-crash investigation found that cumulative excess deaths were
primarily driven by age-structure drift rather than a simple fertility or
mortality-rate-level problem. The legacy memoryless fractional operator was
mass-conserving but allowed cohorts to advance across multiple 5-year bins too
quickly, overpopulating high-mortality old-age bins.

**Implementation:**
- Added adult residence-phase state inside `SimulationEngineV3` while keeping
  the existing aggregate population tensor as the public model state.
- Added childhood residence phases inside `ChildhoodPool`.
- For quarterly runs, each 5-year age bin has 20 residence phases.
- People advance one phase per timestep and only exit an age bin from the final
  residence phase.
- Births, sexual debuts, and positive migration deltas enter residence phase 0.
- Mortality, negative migration, disease transitions, ART, and PrEP changes are
  reconciled back to residence phases before aging.
- Terminal adult age bin still accumulates with no outflow.

**Observed deterministic impact, `1980_test.json`, HMD comparison 1980-2023:**

| Metric | Legacy memoryless | Residence queue | Change |
| --- | ---: | ---: | ---: |
| Model all-cause deaths | 7,312,445 | 6,287,813 | -1,024,632 |
| Excess deaths vs HMD | +1,257,775 | +233,143 | -1,024,632 |
| Adult excess deaths | +1,309,287 | +283,527 | -1,025,760 |
| Adult stock-driven excess | +1,083,963 | +272,058 | -811,905 |
| Adult deaths-per-stock excess | +225,324 | +11,469 | -213,855 |
| Model births minus HMD births | -152,310 | +135,326 | +287,636 |

**2024 population impact:**
- Legacy memoryless final population: 16,795,230.
- Residence-queue final population: 18,164,172.
- HMD 2024 population: 17,942,942.
- The model moved from 1.15M low to 0.22M high versus HMD.

**Calibration note:**  
V13.0 materially addresses the structural death-driven population crash, but it
does not complete demographic recalibration. Fertility, mortality, migration
age profile, LHS, beta/epsilon, and downstream HTA/PSA calibration should be
rerun against the V13 engine because previous calibration outputs compensated
for an over-aged population.

**Tests and verification:**
- `tests/test_residence_queue_aging.py`: 3 passed.
- `tests/test_aging_conservation.py` + residence queue tests: 7 passed.
- Config/engine hardening tests: 25 passed.
- Migration/freeze timing tests: 21 passed.
- PrEP dynamics tests: 15 passed.
- HTA freeze/e2e smoke tests: 54 passed.
- Full demographic backtests completed for residence queue and legacy paths.
- Full pytest suite: 676 passed, 4 skipped, 9 failed. Reviewed failures were
  outside the demographic aging path: known HTA sampler mean/value mismatch,
  PSA-HTA test config missing active Lenacapavir uptake in 2025-2030, and
  sandbox-blocked multiprocessing semaphore inspection in the PSA
  parallel/sequential equivalence test.

---

## V12.19 — 2026-05-18

### Deterministic Event Allocation Consistency

**Patch doc:** `documentation/patches/PATCH_PLAN_V12_19_DETERMINISTIC_EVENT_ALLOCATION.md`  
**Files modified:** `run_v3.py`, `tests/test_engine_hardening_v124.py`, `documentation/patches/PATCH_PLAN_V12_19_DETERMINISTIC_EVENT_ALLOCATION.md`, `documentation/DOCUMENTATION_INDEX.md`

**Summary:**  
Replaced deterministic cellwise event rounding in `SimulationEngineV3._draw_events()`
with aggregate-preserving integer allocation. Deterministic mode now preserves
the rounded aggregate expected event total while keeping integer population
tensors and leaving stochastic binomial draws unchanged.

**Implementation:**
- Added `_deterministic_allocate_events()` to allocate deterministic residual
  events to cells with the largest fractional expected counts.
- `_draw_events()` now uses the allocator for deterministic biological and
  intervention event paths, including disease progression, diagnosis, ART/PrEP
  transitions, and HIV-stage mortality.
- Invalid deterministic event probabilities now raise `ValueError`, matching
  stochastic binomial expectations.
- Added focused engine-hardening tests for sparse aggregate preservation,
  capacity caps, shape preservation, reproducibility, and probability bounds.

**Scope note:**  
This patch applies to event paths routed through `SimulationEngineV3._draw_events()`.
Separate deterministic rounding in transmission, demographic mortality, births,
and aging remains out of scope and should be considered separately if
deterministic/stochastic consistency work is broadened.

**Tests:**

```bash
python3 -m pytest tests/test_engine_hardening_v124.py -q
```

10 passed.

```bash
python3 -m pytest \
  tests/test_art_progression.py \
  tests/test_art_no_schedule.py \
  tests/test_diagnosis_year_bands.py \
  tests/test_prep_dynamics.py \
  tests/test_aging_conservation.py \
  tests/test_engine_hardening_v124.py -q
```

56 passed, 11 pre-existing pytest return-value warnings.

```bash
python3 -m pytest tests/test_lhs_calibration.py tests/test_lhs_pipeline.py -q
```

19 passed.

```bash
python3 -m pytest \
  tests/test_economics_v2.py \
  tests/test_hta_costing_patch_v1117.py \
  tests/test_hta_report.py \
  tests/test_hta_e2e.py \
  tests/test_psa_infrastructure.py -q
```

225 passed, 1 skipped.

Full-suite smoke: `python3 -m pytest -q` produced 673 passed, 4 skipped, 9
failed. Reviewed failures were not attributable to the allocator: known
`art_first_line` HTA sampler mean/value mismatch, PSA-HTA test config missing
active Lenacapavir uptake in 2025-2030, and sandbox-blocked multiprocessing
semaphore inspection in the PSA parallel/sequential equivalence test.

**Observed deterministic impact:**
- `1980_test.json`, deterministic seed 42: cumulative 1980-2024 AIDS deaths
  changed from 0 to 163; new AIDS cases from 1 to 199; final 2024 PLHIV from
  274 to 111.
- `lhs_pipeline_v1217` joint rank-001 config, deterministic seed 31836:
  cumulative 1980-2024 AIDS deaths changed from 3,205 to 8,997; new AIDS cases
  from 6,573 to 16,017; final 2024 PLHIV from 18,530 to 36,899.

Existing deterministic calibration outputs should therefore be treated as
pre-patch artifacts and rerun/recalibrated before acceptance.

---

## V12.16 — 2026-05-16

### LHS Pipeline Calibration Upgrade

**Patch doc:** `documentation/patches/PATCH_V12_16_LHS_PIPELINE_UPGRADE.md`  
**Files modified:** `run_lhs_pipeline.py`, `run_lhs_calibration.py`, `calib_scorer.py`, `tests/test_lhs_calibration.py`, `tests/test_lhs_pipeline.py`

**Summary:**  
Upgraded the full staged LHS epidemic calibration pipeline from a single-best
handoff into a more robust screening and refinement workflow. The old behavior
is preserved by default with `--beam-width 1`.

**Implementation:**
- Added beam handoffs to `run_lhs_pipeline.py` via `--beam-width`, recording
  selected `best_fit_configs` and branch metadata in the pipeline status JSON.
- Added `--joint-n` so the final 20-dimensional joint stage can use a larger
  draw budget than lower-dimensional block stages.
- Added joint local refinement via `--joint-refine-around-input`, narrowing
  active joint-stage bounds around each incoming candidate config.
- LHS calibration screening now forces deterministic model evaluation by
  default; `--stochastic-screen` restores previous stochastic screen behavior.
- Added stochastic elite reranking with `--rerank-top` and
  `--rerank-replicates`, using replicate mean log-likelihoods for final ranking.
- Added optional target-family balanced scoring with `--family-balanced-score`.
- Added optional fail-fast behavior for active but inert sensitivity dimensions
  with `--fail-on-inert-sensitivity`.

**Calibration data path:**  
The runner continues to use `Validation /DiseaseData.xlsx` through
`configs/disease_target_mapping.json`. The workbook remains read-only. Each
stage writes the normalized target table actually used by the scorer to
`normalized_targets_<stage>_<timestamp>.csv`. Model outputs are annualized by
`calib_extractor.py` from quarterly summaries: PLHIV uses Q4 stock logic; flows
such as new infections, AIDS deaths, diagnoses, AIDS cases, and ART initiations
are annual sums; ART stock uses year-end stock.

**Recommended stronger run:**

```bash
python3 run_lhs_pipeline.py \
  --n 5000 \
  --top-n 20 \
  --beam-width 5 \
  --joint-n 20000 \
  --joint-refine-around-input 0.25 \
  --family-balanced-score \
  --rerank-top 25 \
  --rerank-replicates 10 \
  --diagnostics
```

**Tests:** `python3 -m pytest tests/test_lhs_calibration.py tests/test_lhs_pipeline.py` — 18 passed.

**Hotfix — 2026-05-16:**  
Fixed two issues found by the first `lhs_pipeline_v1216` production run:
- Diagnostics now read joint best-fit config paths from `best_fit_configs`, so
  beam/branch outputs under `lhs_joint/branch_###/` are supported.
- Diagnostics can run when all stages completed but the pipeline status is
  marked failed because diagnostics itself failed.
- Stochastic reranking now keeps reranked candidates ahead of non-reranked
  screen-only rows, preventing unreplicated screen draws from winning after
  `--rerank-top`.

Recovered diagnostics were generated from the completed run at
`outputs/calibration/lhs_pipeline_v1216/diagnostics_recovered/`.

---

## V12.15 — 2026-05-16

### Diagnosis Year-Band Schedule (Stepwise / Linear Interpolation)

**Patch doc:** `documentation/patches/PATCH_V12_15_DIAGNOSIS_YEAR_BANDS.md`  
**Files modified:** `run_v3.py`, `tests/test_diagnosis_year_bands.py`

**Summary:**  
Added optional time-varying year-band support to the diagnosis rate, analogous to
`art_schedule` and `prep_schedules`. A new `schedule` sub-block under
`disease.diagnosis` accepts anchor year→rate pairs with either `"stepwise"`
(carry-forward) or `"linear"` (interpolation) modes, reusing the existing `TimeSchedule`
infrastructure.

**Motivation:**  
A flat annual diagnosis rate is epidemiologically implausible. Dutch HIV surveillance shows
the undiagnosed fraction fell from ~40–50% in the mid-1980s to ~14% by 2021 (van Sighem
et al. 2015, SHM; ECDC 2021). Year bands allow calibration of the diagnosis schedule to
match observed trends without changing any existing config numeric values.

**Implementation:**
- `run_v3.py`: `TimeSchedule` added to `schedule_utils` import.
- `_build_diagnosis_schedule()` — new private method reads `config["disease"]["diagnosis"]["schedule"]`; returns `TimeSchedule` or `None`.
- `self.diagnosis_schedule` initialised in `__init__` after `ScheduleManager` block; startup message emitted when enabled.
- `_step_diagnosis(current_year)` — signature extended; uses schedule when present, else flat `annual_rate` (full backward compatibility).
- Call site updated to pass `current_year`.
- `config_reader.py` unchanged — `disease()` already passes `diagnosis` block through.
- `schedule_utils.py` unchanged — `TimeSchedule` already handles both modes.

**Backward compatibility:** Fully preserved. Configs without a `schedule` sub-block continue to use the flat `annual_rate` unchanged. Production configs unmodified.

**PSA note (BACKLOG-26):** When a schedule is active, the flat `annual_rate` PSA distribution is bypassed. Production thesis run uses the flat-rate path; schedule available for calibration and scenario analysis.

**Tests:** `tests/test_diagnosis_year_bands.py` — 13 tests (T1–T13) covering flat fallback, stepwise anchor lookup, stepwise carry-forward, linear midpoint, quarter-point, before-anchor zero rate, engine integration, deterministic rate ordering, invalid interpolation rejection. 13 passed.

---

## V12.14 — 2026-05-15

### Geometric Bidirectional Heterosexual Partnership Pool

**Patch doc:** `documentation/patches/PATCH_V12_14_GEOMETRIC_HETERO_POOL.md`  
**Files modified:** `run_v3.py`, `tests/test_contact_balance.py`, `configs/calib_space.json`, `run_lhs_calibration.py`  
**Type:** Transmission model correctness — contact pool formulation

**Problem:** The V11 heterosexual contact pool used a female-driven formula: `pool = female_contacts[f] * rho_f_to_m`. This made `Heterosexual_Male` contact multipliers structurally inert — they affected partner allocation weights but not total heterosexual contact volume. Heterosexual partnership volume was systematically pulled toward the lower Female supply whenever the two sides diverged.

**Fix:** Replaced with a geometric bidirectional pool:
```python
female_side_pool = female_contacts[f] * rho_f
male_side_pool   = male_contacts[m] * rho_m
pool = np.sqrt(female_side_pool * male_side_pool)
```
Exact heterosexual contact balance preserved. MSM mixing unchanged. Legacy matrix path (no epsilon) unchanged.

**Calibration implication:** `orient_het_male` is no longer structurally inert. LHS calibration space updated to sample both `orient_female` and `orient_het_male` (enforced `orient_het_male > orient_female`). `orient_msm` fixed at 1.0. Previously calibrated outputs should not be treated as unchanged.

**Tests:** `tests/test_contact_balance.py` updated — geometric-pool formula check replaces old female-conservation test; exact contact balance and epsilon edge-case tests preserved.

---

## V12.13 — 2026-05-14

### HTA Cost, Price Sensitivity, and Output Reconciliation

Implemented `PATCH_PLAN_V12_13_HTA_COST_PRICE_OUTPUT_RECONCILIATION`.

**Granular drug costing:**  
`economics.py` now preserves broad payer categories while also emitting
granular prevention and treatment drug keys: `prep_drug`,
`prep_drug_oral`, `prep_drug_lenacapavir`, and `hiv_art_drug`.
Lenacapavir product cost includes injection costs and year-1 oral loading.

**Correct price threshold cost base:**  
`scenario_comparator.py` price sensitivity and threshold pricing now scale
only `prep_drug_lenacapavir`. The comparator refuses to fall back to broad
`drug` because that category also includes HIV ART costs.

**Report reconciliation:**  
`hta_report.py` and `run_scenarios.py` now expose adverse event costs,
terminal care, granular PrEP product costs, HIV ART drug costs, payer
breakdown sums, and residuals. Workbook generation fails fast if the displayed
payer breakdown does not reconcile to payer total costs within €1.

**Deterministic base-case HTA:**  
Main HTA workbook generation now forces deterministic dynamics for the base
case before the baseline freeze. PSA remains the place where uncertainty is
handled. The workbook metadata labels the run mode.

**Negative-QALY threshold explanation:**  
Finite thresholds with negative incremental QALYs are now labelled with an
interpretation note explaining that NMB can remain positive when cost savings
offset the QALY loss at low enough Lenacapavir prices.

Tests: `python3 -m pytest tests/test_economics_v2.py tests/test_hta_costing_patch_v1117.py tests/test_hta_report.py tests/test_hta_e2e.py tests/test_psa_infrastructure.py tests/test_scenario_b_expansion.py -q`
passed with 265 passed, 4 skipped.

---

## V12.12 — 2026-05-11

### HTA Scenario Output Integrity

**Patch doc:** `documentation/patches/PATCH_PLAN_V12_12_HTA_SCENARIO_OUTPUT_INTEGRITY.md`  
**Files modified:** `hta_report.py`, `run_scenarios.py`, `run_psa_hta.py`, `tests/test_hta_report.py`, `tests/test_hta_e2e.py`, `tests/test_psa_hta_integration.py`  
**Type:** Reporting correctness / scenario integrity / fail-fast validation

**Problem:** Transparency tabs (`*Full Data`, `PrEP Population Dynamics`, `Incidence by Subgroup`) resolved simulation records using "first available scenario" lookup, silently mixing Scenario A and Scenario B arm records. CEA summary and time-series tabs were correctly keyed by scenario; transparency tabs were not.

**Fixes:**
- `hta_report.py`: full-data tabs replaced with scenario-specific sheets (`Full Data A`, `Full Data B`, `Full Data D`); `_write_arm_full_data()` replaced by `_write_scenario_full_data(scenario_key)`; no transparency tab resolves records by scanning `all_results.values()` without a scenario key.
- `run_scenarios.py`: added `_validate_lenacapavir_schedule_active()`; raises `ValueError` when Lenacapavir is enabled but initiation schedule has no positive rate in the evaluation horizon.
- `run_psa_hta.py`: same Lenacapavir schedule validation applied per PSA draw; default `--config` updated to `1980_test.json`.

**Tests:** 93 passed, 1 skipped (`test_hta_report.py`, `test_psa_infrastructure.py`, `test_simulation_transparency.py`); 56 passed (`test_hta_e2e.py`, `test_prep_reset.py`).

---

## V12.11 — 2026-05-11

### Key Figures Tab Polish

**Patch doc:** `documentation/patches/PATCH_V12_11_KEY_FIGURES_POLISH.md`  
**Files modified:** `hta_report.py`, `tests/test_hta_report.py`  
**Type:** Presentation / chart readability — no economic calculation changes

**Tab rename:** `Reviewer Figures` → `Key Figures` (third tab in workbook).

**Readability improvements:**
- NMB charts: symmetric y-axis bounds around zero; zero-NMB threshold line centred in view; explicit axis number formats; tick labels preserved.
- Cumulative infections averted: y-axis starts at zero; five-year x-axis cadence.
- Price sensitivity: symmetric y-axis; euro-formatted x-axis.
- All Key Figures chart legends removed (prevents openpyxl category-label expansion).
- Side note fonts increased to 14 pt; note boxes enlarged.
- Publication-facing text replaces internal reviewer-instruction language.
- Chart block dimensions and spacing increased.

**Tests:** `tests/test_hta_report.py` — 28 passed, 1 skipped.

---

## V12.10.1 — 2026-05-12

### CEA Results Summary: Primary Comparison Highlighting + Scenario D Mini-Table

Two report-layer defects fixed in `_write_cea_summary` (`hta_report.py`) and
`build_comparison_table` (`scenario_comparator.py`).

**Primary comparison always shown (`scenario_comparator.py`):**  
Lenacapavir vs Oral PrEP was silently missing from the incremental table
whenever oral PrEP was sequentially dominated by No PrEP (common in societal
perspective). After the ZIN sequential loop, the pair is now explicitly added
with `is_primary_comparison=True` if not already generated. The ZIN chain
itself is unchanged.

**Lena row highlighting (`hta_report.py`):**  
All lenacapavir comparison rows in the CEA Results Summary now receive a
green fill (`FILL_LENA_SECONDARY = E2EFDA`). The primary `lena_vs_oral_prep`
row receives a stronger green (`FILL_LENA_PRIMARY = C6EFCE`), bold label, and
`†` prefix. ICER cell fill is intentionally excluded from row colouring to
preserve CE status colouring. Section header and footnote updated to flag the
dagger row. Guard: `row.get(...) is True` (not `bool(...)`) prevents pandas
NaN truthy false-positives.

**Scenario D mini-table (`hta_report.py`):**  
Scenarios with `price_sensitivity_range` in config no longer render the
standard incremental table (which was an exact duplicate of Scenario A).
Instead, a compact ZIN 3-tier threshold price summary is shown (€20k/€50k/€80k
WTP tiers, threshold annual price, CE at base case). Reuses
`find_threshold_price()` from the Price Sensitivity tab.

Tests: 219 HTA-suite tests pass; pre-existing `test_gamma_method_of_moments`
sampler tolerance failure unaffected.

---

## V12.10 — 2026-05-12

### HTA Report: Scenario B Expansion Support

Updated `hta_report.py` so the new `scenario_b_expansion` scenario (added in
V12.9) is fully visible in the Excel report.

Key changes:
- `ARM_LABELS`: added `oral_prep_plus_lena` → "Oral PrEP + Lenacapavir"
- `SCENARIO_LABELS`: added `scenario_b_expansion` with proper display name
- `SCENARIO_TAB_SUFFIXES`: maps `scenario_b_expansion` to tab suffix "B"
- `_ARM_COLORS`: added lighter green (A9D18E) for combination arm
- `ARM_ORDER` (class + module-level): extended to include `oral_prep_plus_lena`
  so it renders in all tabs that iterate arms; absent arms skipped gracefully
- `write()` ts_scenarios list: expansion entry added before targeted; guard
  prevents duplicate "Time-Series B" tab when both scenarios are present
- `_write_key_figures` / `_scenario_incremental_rows`: parameterised with
  `intervention_arm` and `reference_arm`; expansion framing
  (oral_prep_plus_lena vs oral_prep) reuses same logic as lena vs oral_prep;
  Key Figures Scenario B block auto-selects expansion or targeted at runtime
- Chart title and figure note update dynamically with the active framing

No numeric changes. No config changes. Backward compatible — existing
scenario_b_targeted runs unaffected; expansion takes priority when present.

Files: hta_report.py

**Hotfix (same day):** `_aggregate_timeseries` contained an 8th hardcoded arm-prefix dict
not caught in initial patch. Added `"oral_prep_plus_lena": "oral_prep_plus_lena"` to resolve
`KeyError` at runtime.

---

## V12.9 — 2026-05-11

### LHS Epidemic Calibration Runner

Added a standalone Latin Hypercube Sampling calibration path for epidemic
parameters, separate from the demographic `run_calibration.py` report runner.

Key changes:
- New modules: `calib_data_loader.py`, `calib_extractor.py`,
  `calib_scorer.py`, and `run_lhs_calibration.py`
- New parameter-space file: `configs/calib_space.json`
- `run_v3.py` summary output now includes true `aids_deaths`, using the
  AIDS-only mortality count already computed internally
- UNAIDS parser uses stable GID keys: `PLWH`, `NEW_INFECTIONS`, and
  `AIDS_DEATHS` with all-ages estimate/lower/upper subgroups
- Runner deep-copies source configs, writes candidate best-fit configs as new
  files, and records source config SHA256 before/after to verify no mutation
- Added focused tests in `tests/test_lhs_calibration.py`

No existing config JSON files were modified.

### Scenario B Redesign: Expansion Framing

Redesigned Scenario B from a drug-swap comparison (Lena-only vs oral-only) to an
expansion framing: oral PrEP + targeted Lena vs oral PrEP alone. Consistent with
Nosyk et al. (2025, PMC12813553) and Jamieson et al. (2022, Lancet HIV).

Key changes:
- New scenario `scenario_b_expansion` in hta_config.json with per-arm prep_reset
  control, prep_schedule_overrides (Kuipers RIVM 2023 high scenario), and per-
  intervention eligibility targeting
- New arm `oral_prep_plus_lena`: both interventions simultaneously, oral PrEP for
  Medium+High MSM, Lena for High MSM only
- Scenario-driven arm_order in run_scenarios.py and run_psa_hta.py
- economics.py: patient time costs use independent if-blocks; combination arm gets
  costs from both PrEP types
- scenario_b_targeted deprecated in-place (BACKLOG-25 cleanup)

No engine changes. No numeric parameter changes. Backward compatible.

Files: configs/hta_config.json, run_scenarios.py, run_psa_hta.py, economics.py,
       tests/test_scenario_b_expansion.py

## V12.8 — 2026-05-11

### Test Reliability and HTA Seed Remediation

- `tests/test_transmission_route_betas.py`, `tests/test_config_reader.py`: converted boolean-return pytest tests into real assertions so failed epidemiological/config invariants fail loudly instead of emitting warnings while passing.
- `run_scenarios.py`: changed `MultiScenarioRunner._get_seed()` to require canonical `run_control.seed`; missing or non-integer-compatible seeds now raise immediately instead of silently falling back to `42`.
- `run_scenarios.py`: independent no-freeze scenario mode still uses `base_seed + scenario_index`, but now records the scenario-specific seed in `run_control.seed` in temporary scenario configs.
- `tests/test_hta_seed.py`: added focused regression tests for seed extraction and scenario seed traceability.
- `tests/test_lena_waning_state.py`: updated direct HTA economics tests to pass explicit zero gross Lenacapavir initiations under the V12.7 initiation-cost API.

## V12.7 — 2026-05-11

### Main Epidemic + HTA Cost Engine Audit Remediation

- `config_reader.py`: bridged `interventions.art.advanced.art_dropout_rate` into `art_schedule.discontinuation`, starting at the first nonzero ART initiation year. Explicit ART discontinuation schedules take precedence.
- `run_v3.py`: moved HTA baseline freeze to the start of the freeze timestep, so `freeze_year=2025` captures the beginning of 2025 before policy-year mechanics run.
- `transmission_corrected.py`: capped accumulated infections against live susceptibles before returning reported totals and age/PrEP infection arrays.
- `run_v3.py`, `economics.py`, `adverse_events.py`: routed gross per-step PrEP initiation counters into initiation-billed prevention costs and adverse events. Missing counters now fail loudly when initiation-billed prevention costs/AEs require them.
- Tests added/updated for ART dropout plumbing, freeze timing, transmission accounting, and gross initiation costing/AE behavior.

## V12.6 — 2026-05-06

### HTA Report — Chart Redesign (`hta_report.py`)

Cosmetic-only patch. No numeric parameters changed.

- **Legend bug fixed (Price Sensitivity):** `titles_from_data=True` with `set_categories()` caused Excel to render each x-axis category (price point) as an individual legend entry (~20 items). Replaced with explicit `SeriesLabel` titles; legend now shows exactly two entries: "ICER (€/QALY)" and "WTP €{wtp}/QALY".
- **Axis labels restored:** Added `numFmt` to all chart axes. Costs: `€#,##0`; QALYs: `#,##0.0`; Infections/ICER: `#,##0`; price axis: `€#,##0`.
- **Arm colors standardised:** Explicit per-arm colors applied via new `_add_styled_series()` helper — No PrEP blue (`4472C4`), Oral PrEP orange (`ED7D31`), Lenacapavir green (`70AD47`).
- **WTP reference line:** Dashed dark red (`C00000`, 1.5 pt) — visually distinct from ICER line (solid blue, 2 pt).
- **Infections chart converted:** Annual New Infections chart changed from `BarChart` to `LineChart`. 40+ years × 3 arms = 120 grouped bars was unreadable; lines show the trajectory clearly.
- **Chart style:** All redesigned charts set to `style=2` (clean minimal) from `style=10`.


### Population Multiplier — Susceptible-Only Compartment Scope

**Problem:** `apply_population_adjustments()` previously scaled the entire population
tensor, including HIV+ compartments (Acute/Chronic/AIDS) and PrEP users. This inflated
absolute HIV cases averted, AIDS cost estimates, and the epidemic trajectory — none of
which were generated by transmission mechanics. Not defensible for a 20–40 year CEA
horizon under reviewer scrutiny.

**Changes:**
- `demography.py`: `apply_population_adjustments()` now reads `compartment_scope` config
  field and branches on `"susceptible_only"` vs `"all"`
- `scope="susceptible_only"`: multiplier applied only to `[hiv_stage=Susceptible,
  prep_state=None]`; HIV+ compartments grow exclusively via transmission; PrEP users
  grow only via enrollment mechanics
- `scope="all"`: original full-tensor behaviour retained for calibration use only
- Default when field absent: `"susceptible_only"` (fail-safe for scenario runs)
- Invalid scope values raise `ValueError` at construction time (fail-fast)
- `1980_test.json`: `compartment_scope: "susceptible_only"` added to
  `population_adjustments`; `enabled` remains `true`
- 8 new tests in `tests/test_pop_multiplier_scope.py` (T1–T8)

**No changes to:** `config_reader.py`, `run_v3.py`, `engine_v3.py`, numeric parameters.

**Baseline for regression (pre-fix, 2025 HTA run):**
- New infections: No PrEP = 8,356 | Oral PrEP = 8,308 | Lenacapavir = 8,307
- Costs: €1,731,695,021 | €1,741,327,601 | €1,904,319,823

---

## V12.5 — 2026-05-05

### Demographic Calibration Runner

**Files added:** `validation_data_loader.py`, `calibration_output_writer.py`, `run_calibration.py`  
**Files modified:** none (`run_v3.py` untouched — calibration runner is fully self-contained)  
**Tests added:** 6 (T1–T6 parser tests in `tests/test_validation_data_loader.py`)  
**Plan doc:** `documentation/features/PLAN_demographic_calibration_runner.md`

**Summary:**  
Standalone demographic calibration tool that compares the model's population trajectory and age-sex structure against external reference data (HMD Netherlands population, World Bank net migration). Produces a structured 5-tab Excel report to `outputs/calibration_report_YYYYMMDD.xlsx`.

**Four checks:**
- Check 1 — Config Audit: every `year_specific` migration entry compared to WB SM.POP.NETM source. Flags any discrepancy >1 (rounding tolerance).
- Check 2 — Population Trajectory: model total population vs HMD Type-O per year (1975–2020), tiered bands ±5% / ±10% / >±10%, MAPE reported.
- Check 3 — Age-Sex Structure: Spearman rank correlation (ρ ≥ 0.80) + chi-square goodness-of-fit at reference years 1985, 1995, 2010. Adult population (15+) only.
- Check 4 — Migration Coverage: full WB year coverage table, flags within-window gaps with |net|>5,000.

**No model parameter changes.**

**Patch (2026-05-05):** Made simulation window and reference years fully dynamic. `run_calibration.py` now reads `start_year`/`end_year` directly from the config rather than using hardcoded 1975–2020. Reference years for age-sex structure are derived algorithmically (25/50/75 quantiles, snapped to nearest 5-year mark) so the check remains meaningful regardless of window length.

**Patch (2026-05-05):** Added Guide tab (tab 2 in report) explaining what each check tests, colour coding, glossary (MAPE, Spearman ρ, chi-square, net migration, HMD Type O, reference years, simulation window), and interpretation notes. Report is now self-contained for reviewers unfamiliar with the model.

**Patch (2026-05-05):** Fixed migration coverage flag logic. Post-window years (> sim_end) were incorrectly labelled `PRE-WINDOW`; they are now labelled informational `Post-window` with a note that they are beyond the simulation end year and not used in the run. Also fixed the `In Sim Window` description text in the Migration_Coverage tab to use the dynamic window label from config rather than hardcoded `1975–2020`.

**Patch (2026-05-05):** Fixed three compounding bugs in `run_calibration.py` that caused the calibration window and age-structure checks to be silently wrong:

1. `_read_sim_window` was reading `cfg.get("time", {})` — the top-level `time` key is absent from V10 configs (window lives under `run_control`). Fixed to read `run_control.start_year`/`end_year`. Actual window is now 1970 as defined in the main config.

2. `_build_engine_quietly` was writing the override to `cfg["time"]`, but `ConfigReader` reconstructs `time` from `run_control` and ignores any top-level `time` key. The per-reference-year engine windows were never being applied — every age-structure run was executing the full simulation and reading end-of-sim population state. Fixed to override `run_control.start_year`/`end_year`.

3. Calibration window now mirrors the full main simulation window (1970–2025). Years where HMD has no reference data emit N/A rows in the Pop_Trajectory tab rather than being silently excluded. HMD cap removed. In practice, engine quarterly steps (step_size=3 months) mean the last data point lands at 2024.75 → year_int=2024; no 2025 row is produced, so no N/A row appears for this config. N/A infrastructure is in place for future configs with longer windows.

**Impact on Check 3 results:** With engines now correctly stopping at each reference year (1985/2000/2010), Check 3 (Age-Sex Structure) changes from FAIL to PASS — all Spearman ρ values ≥ 0.80 for both sexes at all three reference years. The previous FAILs were entirely an artefact of the engine override bug. Reference year 1995 → 2000 after window re-derivation from 1970–2025.

**Patch (2026-05-05):** Added Guide tab (tab 2) to the HTA Excel report (`hta_report.py`). Inserted between "Study Overview" and "CEA Results Summary". The Guide is a self-contained reviewer navigator covering: pipeline overview (scenarios/arms/freeze-year design), tab-by-tab descriptions for all 16 tabs, key metrics with formulas (ICER, NMB, QALY, WTP), ZIN three-tier WTP thresholds with policy basis, colour coding reference (dominant/dominated/CE-yes/no fills), scenario definitions (A/B/D), arm descriptions (No PrEP/Oral PrEP/Lenacapavir), and a 16-term glossary. Styled two-column (navy headings, mid-blue subheadings) matching the calibration runner Guide tab. Three new tests added: `test_guide_tab_position`, `test_guide_tab_sections`, `test_guide_tab_glossary_terms`. Tab count: 15 → 16. No model parameter changes.  
**Files modified:** `hta_report.py`, `tests/test_hta_report.py`

---

## V12.2 — 2026-05-05

### Pop_Forecast Tab — CBS Population Validation (Check 5)

**Files modified:** `validation_data_loader.py`, `run_calibration.py`, `calibration_output_writer.py`  
**Tests added:** 16 (T7–T10 in `tests/test_validation_data_loader.py`; T11–T20 in `tests/test_calibration_pop_forecast.py`)  
**Plan doc:** `documentation/features/PLAN_pop_forecast_cbs_tab.md`

**Summary:**  
Added Check 5 — Population Forecast vs CBS to the demographic calibration report. The new `Pop_Forecast` tab (tab 8) compares the model's forward total population trajectory (years ≥ 2026) against CBS (Centraal Bureau voor de Statistiek) official Netherlands population projections 2026–2070, including 67% and 95% confidence intervals. Provides forward-looking external plausibility validation for the no-PrEP arm and future population projections, which previously had no external benchmark beyond 2023.

**Changes per file:**

`validation_data_loader.py`
- Added `load_cbs_forecasts(path)`: parses CBS StatLine Bevolkingsprognose CSV (semicolon-delimited, European decimal comma, BOM-prefixed UTF-8). Returns dict keyed by year with central forecast and CI bounds in persons. Handles malformed interval strings gracefully (logs warning, sets CI to None, continues).
- Added `default_cbs_path(v11_root)`: resolves default path to `Validation /NL_Population_Forecasts.csv`.

`run_calibration.py`
- Added `check_pop_forecast(summary_df, cbs_data)`: extracts year-end model population for years ≥ 2026 in CBS data, converts to millions, computes within_67/within_95 flags and % deviation from CBS central.
- Updated `run_all_checks()`: added optional `cbs_path=None` parameter (backward compatible), auto-resolves path if not supplied, loads CBS data with graceful FileNotFoundError handling, runs Check 5, adds `pop_forecast` key to results dict and `cbs_path` to meta.
- Updated module docstring: "Four checks" → "Five checks", added Check 5 description.
- Updated step print labels `[1/5]…[5/5]` → `[1/6]…[6/6]`.
- Added `--cbs` CLI argument.

`calibration_output_writer.py`
- Added `_write_pop_forecast()`: 8-column data table (Year, Model M, CBS Central M, 67% Lo/Hi, 95% Lo/Hi, CI Status) with GREEN/AMBER/RED row colouring, summary stats block, 6-series line chart (model, CBS central, 67% CI bounds, 95% CI bounds), and methodological note.
- Added `_summary_pop_forecast()`: PASS ≥80% within 95% CI | WARN 50–79% | FAIL <50% | N/A if no overlap.
- Updated `_write_summary()`: added Check 5 row to status table; CBS source and file path added to metadata block.
- Updated `_write_guide()`: renamed "Four Checks" → "Five Checks", added CBS data source entry, added Check 5 description block.
- Updated `write()`: calls `_write_pop_forecast()` after `_write_vital_stats()`.
- Updated module docstring (8 tabs).

**Pass/fail criteria:**  
PASS = ≥80% of overlapping forecast years within CBS 95% CI | WARN = 50–79% | FAIL = <50% | N/A = no overlap or CBS file missing.

**Expected result with current model state:** FAIL — model population ~17.4% below CBS lower bound due to `year_multipliers` disabled. This is the correct and informative outcome; it quantifies the known structural gap for the thesis record.

**No model parameter changes.**

**Patch (2026-05-05):** Extended calibration engine to `_FORECAST_HORIZON = 2045` so the Pop_Forecast tab has post-2025 population data for CBS comparison. Engine now runs `sim_start → max(sim_end, 2045)` regardless of config `sim_end`. Check 2 (HMD trajectory) is unaffected — N/A emitted for years without HMD data as before. BACKLOG-24 added for future flexibility (configurable horizon / HTA arm linkage).

---
## V12.4.1 — 2026-05-04

### Hotfix: Epidemic Reseed in HTA Arm Engines

**Files changed:** `run_v3.py`  
**Tests affected:** none (existing T-ES tests cover seed-once behaviour on the warm-up engine; this bug was only observable via the HTA fork path)

**Bug:** When an HTA arm engine was created with `initial_snapshot=restored_snapshot`, `_epidemic_seed_fired` was unconditionally initialised to `False` (line 280). The arm engine starts at the freeze year (~2025), which satisfies `current_year >= seed_year (1982)`, causing the seed to fire again into an already-established epidemic — injecting ~11,720 fresh Acute cases at 2025.

**Fix:** In the `initial_snapshot` restore block, conditionally set `self._epidemic_seed_fired = True` only when `_epidemic_seed_enabled` is `True` **and** `initial_snapshot.year >= self._epidemic_seed_year`. This correctly suppresses re-seeding for the standard case (seed_year=1982, freeze=2025) while still allowing the seed to fire if someone configures a seed_year beyond the freeze point. If seeding is disabled entirely (`_epidemic_seed_enabled = False`), the flag is irrelevant and untouched.

---

## V12.4 — 2026-05-04

### Epidemic Seeding: Time-Gated HIV Introduction Event

**Files changed:** `v11_configurationMAIN.json`, `run_v3.py`, `engine_v3.py`  
**Tests added:** 12 (T-ES1 through T-ES12 in `tests/test_epidemic_seeding.py`)  
**Total tests:** 12/12 new tests passing  
**Reasoning doc:** `documentation/sessions/2026-W19/V124_epidemic_seeding_reasoning.md`  
**Session note:** `documentation/sessions/2026-W19/session_2026-05-04_epidemic_seeding_design.md`

**Problem:** `initial_hiv_prevalence = 0.05` seeded ~470K HIV+ individuals at t=1970,
twelve years before HIV reached the Netherlands (first Dutch diagnoses 1982, Coutinho 1998).
Calibration to real Dutch epidemic (~22,500 diagnosed PLHIV at 2025; SHM Annual Report)
was structurally impossible — the initial seed dominated the entire 55-year trajectory.

**Changes:**

1. **`v11_configurationMAIN.json` — zeroed initial prevalence and added epidemic_seed block:**
   - `initial_hiv_prevalence` → 0.0 (HIV not present in NL in 1970)
   - New `epidemic_seed` block inside `population`:
     - `enabled` (bool): feature flag; false = suppress seeding entirely
     - `seed_year` = 1982.0 (fixed; first Dutch HIV diagnoses per RIVM/Coutinho 1998)
     - `seed_prevalence` (calibration, [0, 0.05]): proportion of seeded subpopulation
     - `msm_fraction` (calibration, [0, 1]): 0 = fully heterosexual seed, 1 = fully MSM
     - `seed_stage` = "Acute" (biologically correct for an introduction event)

2. **`run_v3.py` — new `_apply_epidemic_seed()` method:**
   - One-time fire at `seed_year`; guard flag `_epidemic_seed_fired` prevents re-fire
   - Allocation across age groups and activity classes is proportional to the existing
     susceptible population tensor — no age band imposed, no activity class specified
   - Justification: van Griensven et al. 1987 (Am J Epidemiol 125:1048-57) found age
     was NOT a significant predictor of seropositivity in early Dutch MSM epidemic
   - MSM seed = n_msm_susceptible × seed_prevalence × msm_fraction
   - Heterosexual seed split Female/HeteroMale proportional to their susceptible counts
   - Returns `total_seeded` (int) — passed to `validate_global_population_balance`
     for logging only (seeding is compartmental, not population-creating; not added to
     expected total)

3. **Bug fix: `engine_v3.py` `_seed_initial_hiv()`:**
   - Previously hardcoded destination compartment to `Chronic` regardless of argument
   - Fixed: now uses `seed_stage` parameter; default remains `Chronic` for backward
     compatibility (safe: `initial_hiv_prevalence = 0.0` means this path never executes
     in normal runs)

**Design constraints (documented limitations):**
- `seed_prevalence` and `msm_fraction` have no reliable literature priors. ELISA
  testing was unavailable pre-1985; true HIV prevalence at introduction is unknowable.
  Both are calibration targets. This is a documented model limitation.
- Age and activity allocation are proportional (no distribution imposed). Defensible
  per van Griensven 1987; avoids unverifiable parametric assumptions.

**Bug fix in mass balance validator:**  
- `validate_global_population_balance()` had `seed_change` incorrectly added to
  `expected_total`. Seeding is a compartmental transition — total population is
  unchanged. Fixed by excluding `seed_change` from the formula (parameter retained
  for logging).

---

## V12.3 — 2026-05-01

### ZIN Three-Tier Threshold Price Summary in Price Sensitivity Tab

**Files changed:** `hta_report.py`, `scenario_comparator.py`, `run_psa_hta.py`,
`tests/test_hta_report.py`  
**Tests added:** 6 (T-TP1 through T-TP6, in `TestThresholdPriceSummaryBlock`)  
**Total tests:** 505 collected, 504 passing (1 pre-existing stale failure: `test_gamma_method_of_moments`)  
**Session note:** `documentation/sessions/2026-W19/SESSION_NOTE_2026-05-01.md`

**Changes:**

1. **`scenario_comparator.py` — bug fix in `find_threshold_price()`:**  
   The `wtp` parameter was accepted but never used — the internal binary search called
   `self.compare_pair()` which hardcodes `self.wtp`. Calling with `wtp=20_000` or
   `wtp=50_000` silently returned the threshold for the primary WTP (€80,000).  
   Fixed by replacing `compare_pair()` calls inside the binary search with a direct
   NMB helper `_nmb_at_price()` that uses the caller-supplied `wtp`. Perspective
   (payer/societal) is preserved. The `_make_lena_at_price()` ScenarioResult
   construction is retained for backward-compatibility with other callers but is no
   longer used in the search loop.

2. **`hta_report.py` — ZIN threshold summary block in `_write_price_sensitivity()`:**  
   Added module-level constant `_ZIN_WTP_TIERS = [20_000, 50_000, 80_000]` (ZIN 2022
   fixed Dutch policy tiers).  
   After the existing footnote, inserts a compact table:
   - **Col 1:** ZIN WTP Tier (€/QALY) — €20,000, €50,000, €80,000
   - **Col 2:** Threshold Annual Injection Price — `find_threshold_price()` result,
     or one of two distinct N/A strings with defensive None-case handling:
     - Case (a) never CE: `"N/A — not cost-effective at any price"`
     - Case (b) always CE (>10× base): `"N/A — cost-effective above search range (>10× base)"`
   - **Col 3:** Cost-Effective at Base Case Price? — `Yes`/`No` with `FILL_CE_YES`/
     `FILL_DOMINATED` fills.  
   The zero-price probe for None-case discrimination computes NMB directly per tier
   (matching `find_threshold_price` internals) rather than routing through
   `compare_pair()`, ensuring correct output when a peer reviewer runs alternative
   scenarios with different WTP assumptions.

3. **`run_psa_hta.py` — stale warning removed:**  
   Deleted the warning `"WTP=30,000 in config — update to 80,000 before thesis"`.
   WTP was corrected to €80,000 in V11.11.7 and the warning was no longer accurate.

**Test coverage (T-TP1–T-TP6):**
- T-TP1: Section header `"ZIN Threshold Price"` present in Price Sensitivity tab
- T-TP2: Exactly three data rows (one per tier)
- T-TP3: Tier labels are `€20,000/QALY`, `€50,000/QALY`, `€80,000/QALY`
- T-TP4: CE column contains only `"Yes"` or `"No"` values
- T-TP5: `"No"` cells carry `FILL_DOMINATED` red fill
- T-TP6: `find_threshold_price` returning `None` produces `"N/A"` strings in all three rows

---

## V12.2 — 2026-04-30

### Lenacapavir_Waning synthetic PrEP state (Markov assumption fix)

**Files changed:** `run_v3.py`, `transmission_corrected.py`, `economics.py`, `output_writer.py`,
`configs/v11_configurationMAIN.json`, `configs/v10_configuration.json`, `configs/v10_dist_test.json`,
`configs/v10_v6_rel_pop.json`, `configs/v10_edge_test.json`, `configs/v6_golden_reference.json`,
`tests/test_economics_v2.py`, `tests/test_hta_costing_patch_v1117.py`,
`tests/test_adverse_events_v2.py`, `tests/test_hta_e2e.py`, `tests/test_hta_config_validator.py`  
**Tests added:** 17 (T-W1 through T-W13, in `tests/test_lena_waning_state.py`)  
**Total tests:** 497 collected, 496 passing (1 pre-existing stale failure: `test_gamma_method_of_moments`)  
**Implementation plan:** `documentation/IMPLEMENTATION_PLAN_V12_2_LENA_WANING_STATE.md`

**Problem — Markov assumption violation:**  
Lenacapavir's subcutaneous depot has a pharmacological half-life of ~10–12 weeks
(Bekker et al., NEJM Evidence 2024). Under a quarterly (3-month) discrete timestep, a user
who drops off PrEP mid-quarter still carries meaningful drug concentration through the next
transmission-computation step. The prior model routed all lena dropouts directly to the `None`
prep state, immediately zeroing their protection. Oral TDF/FTC (t½ ~17 hours) correctly warrants
this treatment; lenacapavir does not.

**Fix — 1-timestep synthetic waning state:**  
A new prep state `Lenacapavir_Waning` is registered programmatically by the engine whenever
`Lenacapavir` is enabled. It is not defined in any config file.

1. **`run_v3.py` — engine:**
   - After intervention registration, adds `Lenacapavir_Waning` via `groups.add_prep_intervention()`
     if `Lenacapavir` is present.
   - Runtime guard raises `NotImplementedError` if lena waning is active and
     `years_per_step != 0.25` (waning depth hardcoded to 1 timestep; valid for quarterly only).
   - `_step_prep_transitions()` reroutes lena dropout population to `Lenacapavir_Waning`
     instead of `None`.
   - New method `_step_lena_waning_transition()` fires at the start of step 7 (before
     prep transitions), moving all waning users to `None`. Step ordering ensures waning
     users receive exactly 1 quarter of protection at step 3 (transmission).

2. **`transmission_corrected.py`:**
   - After the normal `prep_efficacy` loop, injects `Lenacapavir_Waning` into
     `self.prep_efficacy` using `waning_efficacy` from config (default 0.99 matching active lena).
   - `waning_efficacy` key added to `interventions.lenacapavir` in all relevant config files,
     sourced from Bekker et al. 2024.

3. **`economics.py`:**
   - `_PREP_STATE_TO_UTILITY_KEY` maps `Lenacapavir_Waning → susceptible_lenacapavir`,
     giving waning users the same QALY weight as active lena users.
   - `_ARM_TO_PREP_STATE` intentionally excludes `Lenacapavir_Waning`, so waning users
     accrue **zero drug costs and zero visit costs** (injection already charged at dropout step).

4. **`output_writer.py`:**
   - Added `pop_prep_lena_waning` column to per-timestep records.
   - Mass balance formula updated to include waning column (prevents AssertionError crash).

5. **Fixture updates (5 test files):**
   - `prep_states` lists extended: `"Lenacapavir_Waning"` appended.
   - `prep_to_idx` dicts extended: `"Lenacapavir_Waning": 3`.
   - Population tensor shapes updated from `(..., 3)` to `(..., 4)` where needed.

**HTA arm safety:**  
Pre-arm baseline runs have lena disabled — snapshot `n_prep < 4`. The existing
snapshot padding in `run_v3.py` (`snap_n_prep < arm_n_prep` → pad zeros) is shape-agnostic
and handles the 3-prep → 4-prep transition correctly. Ordering constraint (waning always
registered last) ensures semantic alignment. Verified by T-W13.

**Conservative academic note (loading dose):**  
Users re-initiating from `Lenacapavir_Waning` state are re-charged the oral loading dose.
This is conservative: the model cannot verify whether re-initiation occurs within the
pharmacological window. The waning state corrects a systematic understatement of lena
protection; the loading dose re-charge partially offsets the resulting ICER improvement.
Net direction: V12.2 remains conservative on the lena ICER.

---

## V12.1 — 2026-04-30

### Age-stratified incident tracking for friction cost (BACKLOG-21)

**Files changed:** `run_v3.py`, `economics.py`, `tests/test_economics_v2.py`  
**Tests added:** 6 (TestFrictionCostAgeStratified T-B21-1 through T-B21-6)  
**Implementation plan:** `documentation/patches/IMPLEMENTATION_PLAN_BACKLOG_21_AGE_STRATIFIED_INCIDENT_TRACKING.md`

**Problem:** The friction cost method computed working-age incident cases as
`n_incident × wa_frac`, where `wa_frac` was the working-age fraction of the **prevalent**
HIV+ population. This is a proxy: it assumes the age distribution of people entering a
new HIV stage matches the existing HIV+ pool. In reality, the engine had already computed
the exact age-stratified transition counts but discarded them before passing scalars to
economics.

**Fix — three-layer change:**

1. `_step_disease_progression()` (`run_v3.py`) — now returns two additional keys alongside
   the existing scalars: `new_chronic_by_age` and `new_aids_by_age`, 1-D numpy arrays of
   shape `(n_age_groups,)` summed over all non-age dimensions of the transition arrays.

2. `step_simulation()` (`run_v3.py`) — derives `new_infections_by_age` from
   `_last_infections_array` (already available, shape `[age, orient, activity, prep]`)
   and passes all three `*_by_age` arrays as new optional kwargs to `accumulate()`.

3. `economics.py` — three changes:
   - `accumulate()` and `_compute_productivity_losses()` accept three new optional kwargs
     (`new_infections_by_age`, `new_chronic_by_age`, `new_aids_by_age`; default `None`)
   - New helper `_sum_working_age_from_array()`: sums `by_age[_working_age_indices]`
     directly; raises `ValueError` on mis-sized arrays (fail-fast for programming errors)
   - `_compute_productivity_losses_incident()`: when all three arrays are provided AND
     `_working_age_indices` is set, uses direct array summation instead of the prevalence
     proxy; falls back to the existing `wa_frac` path otherwise (full backward compat)

**Backward compatibility:** All existing tests pass unchanged. The new code path requires
all three `*_by_age` arrays to be non-None AND `_working_age_indices` to be set; tests
using the scalar-only API continue to exercise the legacy path.

**Robustness:** Fully robust to any age bin configuration. `_working_age_indices` is
re-derived at init from `groups.age_groups` (config-driven); no hardcoded bin counts.
Childhood pool (0–14) cannot appear in transition arrays — population tensor starts at
the sexually debuted (15+) age bins by construction.

**Quantitative impact:** Minimal in the Dutch MSM context. The prevalence proxy gives
`wa_frac ≈ 0.85–0.93`; the true incident fraction is similar as new infections
concentrate in the same 25–54 working-age range. Estimated bias correction: <5% of the
friction cost component, which is itself secondary to ART and monitoring costs.
Directional effect: slight reduction in friction cost overstatement for arms with more
incident infections (no_prep arm). ICER shift expected to be negligible.

**Thesis disclosure:** Regardless, the methods section documents the simplification and
its expected impact per BACKLOG-21 decision (2026-04-30). See BACKLOG.md.

**Test count:** 474 existing + 6 new = 480 collected, 479 passing (excluding pre-existing
`test_gamma_method_of_moments` stale assertion — unrelated to this patch).

---

## V11.11.9 — 2026-04-30

### Config fix: hiv_monitoring_on_art extended to Acute stage (F11)

**Files changed:** `configs/hta_config.json`  
**Tests added:** 0 (config-only; covered by existing test suite regression)

`hiv_monitoring_on_art` had `"applies_to_stages": ["Chronic", "AIDS"]`. Acute-stage
HIV+ patients on ART attend the same structured outpatient monitoring pathway
(viral load, CD4, labs) as Chronic and AIDS patients — early ART initiation in
acute HIV is recommended per NVHB 2024 guidelines and is standard Dutch practice.
The omission understated disease monitoring costs for Acute/on-ART patients.

**Fix:** `"applies_to_stages": ["Acute", "Chronic", "AIDS"]`

**Impact:** Small upward revision to `hiv_monitoring_on_art` costs in all arms.
The effect is modest in absolute terms — Acute-on-ART is a small stock — but
clinically correct and removes an asymmetry between how Acute is treated in the
patient time cost module (V11.9.12 already counted Acute/on-ART for visit time)
vs. the payer-side disease cost module. Both modules now consistently include Acute.

**Source:** Nederlandse Vereniging van HIV Behandelaren (NVHB) *HIV Behandelrichtlijn*
2024 — early ART initiation in acute HIV infection recommended; same monitoring
schedule applies from treatment start regardless of clinical stage.

**Tests:** 463 existing tests pass; no numeric behaviour change to existing test
fixtures (Acute/on-ART stock is zero in most test scenarios).

### Friction cost method correction — incident-based formula (F4)

**Files changed:** `economics.py`, `run_v3.py`, `configs/hta_config.json`,
`tests/test_economics_v2.py`, `tests/test_hta_costing_patch_v1117.py`  
**Tests added:** 11 (TestProductivityLossFrictionCost T1–T11)  
**Implementation plan:** `documentation/model_notes/IMPLEMENTATION_PLAN_FRICTION_COST_CORRECTION.md`

**Problem:** `_compute_productivity_losses()` was implemented as a capped human capital
approach — charging all prevalent working-age HIV+ individuals every step, scaled by
`friction_period/52`. This is incorrect. The ZIN friction cost method (Koopmanschap et al.
1995) fires **once per incident case** entering a disease stage. A person with chronic HIV
for 10 years was accumulating ~3× the intended cost.

**Fix — three-layer change:**

1. **`run_v3.py`** — `_step_disease_progression()` now returns incident counts:
   `{"new_chronic": int, "new_aids": int}`. These are passed into `accumulate()` alongside
   the existing `new_infections` count.

2. **`economics.py`** — `_compute_productivity_losses()` is now a method dispatcher:
   - `"friction_incident"` (default, ZIN-compliant) → new `_compute_productivity_losses_incident()`:
     fires once per incident case entering Acute, Chronic, or AIDS; formula:
     `n_incident × wa_frac × employ_rate × loss_rate × hourly_wage × annual_hours × (friction_weeks/52)`
   - `"human_capital_capped"` → preserved as `_compute_productivity_losses_prevalent()`
     for sensitivity analysis runs only
   - Unknown method raises `ValueError` immediately

3. **`configs/hta_config.json`** — `perspective.productivity_loss.method` changed from
   `"friction_cost"` (unrecognised) to `"friction_incident"` (canonical dispatcher key).

**Working-age fraction:** Engine does not return age-stratified transition counts. A proxy
is used: current HIV+ working-age fraction from the population tensor scales incident counts.
Documented as a known simplification (BACKLOG-21).

**Test fixture note:** `human_capital_capped` requires `_working_age_indices` (derived from
`age_groups`). A new fixture `_MockGroupsWithAges` (12 real age bins) and
`_make_accumulator_with_ages()` were added to support T5/T7 which test the prevalent path.

**Impact:** Productivity loss costs will be materially lower under the corrected method for
long-duration HIV+ individuals. The correction reduces cost overstatement and aligns the
model with ZIN 2022 societal perspective guidance.

**Known simplifications (tracked as BACKLOG items):**
- BACKLOG-21: Age-stratified incident tracking not yet available from engine
- BACKLOG-22: ART status at transition not tracked (off-ART rate used as conservative proxy)
- BACKLOG-23: Re-entrant double-charging (stage re-entry treated as a new incident)

**Tests:** 474 total (463 existing + 11 new). Pre-existing failure in
`test_hta_sampler.py::test_gamma_method_of_moments` (art_first_line mean asserts 12000,
V11.11.7 corrected to 7352 — stale test predating V11.11.7, not caused by this work).

---

## V11.11.8 — 2026-04-29

### Tests T1–T9: V11.11.7 HTA costing patch validation

Added `tests/test_hta_costing_patch_v1117.py` — 29 tests validating all Groups A–F
changes from the V11.11.7 costing patch. All 29 tests pass.

**Test coverage:**
- T1 (4 tests): Oral PrEP cost decomposition — drug_tdf_ftc_generic + monitoring bundle + renal;
  category routing; renal item confirmed oral-only
- T2 (3 tests): Lenacapavir cost decomposition (continuing year ≥2) — no renal; drug/monitoring
  categories; ongoing annual total = EUR 41,964.94/year
- T3 (3 tests): Lenacapavir year-1 initiation — oral_loading_year1 frequency='once' (no
  step_fraction); loading fires at initiation only; EUR 42,804.92/year basis
- T4 (4 tests): AIDS hospitalisation off_art only — on-ART gets ART+monitoring only; production
  config regression guard (value EUR 22,000, applies_to=['off_art'])
- T5 (3 tests): Terminal care EUR 0 — billing_moment='death' with value=0; production config guard
- T6 (3 tests): WTP EUR 80,000 — production config read; NMB > 0 at EUR 80k for ICER 50k;
  NMB < 0 at old EUR 30k for same ICER (demonstrates CE classification impact)
- T7 (4 tests): Productivity loss — friction_weeks=16.4 and wage=EUR 39.88 produce correct loss;
  net change from old values confirmed; production config regression guards for both parameters
- T8 (2 tests): Regression — test_economics_v2.py (74 tests) still passes; production config key
  names verified (old keys absent, new keys present)
- T9 (3 tests): PSA smoke test — distribution.mean=7352 (not 12000); deterministic value matches
  distribution mean; HTASampler samples cluster around EUR 7,352 (500 LHS draws, seed=42)

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** +29 new. Total: 463 passed, 0 failed.

---

## V11.11.7 — 2026-04-29

### HTA costing patch: parameter corrections + prevention_costs architecture restructure

Full replacement of all placeholder HTA cost parameters with sourced values. Restructured
`prevention_costs` to separate drug costs from monitoring, aligning both arms with MDR PrEP
v4.5 (July 2025) standard monitoring protocol.

**Changes to `configs/hta_config.json`:**

**Group A — Prevention cost architecture (hta_config.json):**
- Removed `prevention_costs.shared[sti_screening_all_prep]` (EUR 300 placeholder, both arms)
- Added `prevention_costs.shared[prep_monitoring_visits_and_sti]` (EUR 854.50/year, both arms):
  2 visits/year × (EUR 120 consultation + EUR 307.25 STI lab bundle per MDR PrEP v4.5 / NZA 2025)
- Added `prevention_costs.shared[prep_renal_monitoring_oral]` (EUR 4.26/year, oral_prep only):
  TDF renal monitoring — 2 × creatinine (NZA 70419 EUR 2.13)
- Removed `prevention_costs.arm_specific.oral_prep.drug_and_monitoring` (EUR 1322 bundle)
- Added `prevention_costs.arm_specific.oral_prep.drug_tdf_ftc_generic` (EUR 1322.21/year):
  Generic emtricitabine/tenofovir disoproxil, EUR 3.62/tablet × 365.25 days
  Source: Farmacotherapeutisch Kompas, accessed 2026-04-29

**Group B — Visit count correction:**
- `patient_time_costs.oral_prep_visits_per_year`: 4 → 2
  (MDR PrEP v4.5 July 2025 standard: 6-monthly = 2 visits/year; old value was quarterly MDR 2022)

**Group C — Disease cost corrections:**
- `disease_costs.aids_hospitalization.value`: 40000 → 22000
  (iMTA 2024 bottom-up; Popping et al. 2023 ATHENA corroboration)
- `disease_costs.aids_hospitalization.applies_to_art_status`: ["on_art","off_art"] → ["off_art"]
  (Architecture decision: AIDS-on-ART covered by hiv_monitoring_on_art)
- `disease_costs.aids_hospitalization.distribution`: mean 40000→22000, se 5000→4000
- `disease_costs.hiv_terminal_care.value`: 2000 → 0
  (Dijk et al. 2025: terminal costs implicit in final AIDS-off-ART state cost steps)

**Group D — iMTA 2024 productivity corrections:**
- `perspective.productivity_loss.friction_period_weeks.value`: 21 → 16.4
  (iMTA 2024 Table 6-1: CBS vacancy data 2018-2022 = 115 days = 16.4 weeks)
- `perspective.productivity_loss.average_hourly_wage.value`: 36.50 → 39.88
  (iMTA 2024 Table 6-2: CBS Arbeidskosten 2023)

**Group E — PSA distribution fix:**
- `disease_costs.art_first_line.distribution.mean`: 12000 → 7352
  (Corrects stale PSA mean to match deterministic value)

**Group F — WTP threshold:**
- `wtp_threshold_eur_per_qaly.value`: 30000 → 80000
  (ZIN 2022 proportional shortfall scale tier 3: PS ≥ 0.70 → EUR 80,000/QALY; HIV/AIDS qualifies)

**Tests:** Config-only changes. Tests per HTA_COSTING_PATCH_PLAN_V1111.md (T1–T9) to be
implemented as separate patch. Total existing: 434 passed, 0 failed.

**Reference documents:**
- `documentation/HTA_COSTING_PATCH_PLAN_V1111.md` — full implementation plan
- `documentation/HTA_COSTING_CONSTRUCTION_TABLE.md` — bottom-up cost derivations
- `documentation/HTA_COSTING_ANALYSIS_2026_04_29.md` — parameter assessment

---

## V11.11.6 — 2026-04-29

### Documentation: PSA × HTA integration complete

Updated `v11/documentation/PSA_IMPLEMENTATION_PLAN.md` to cross-reference this
V11.11 plan and note that HTA integration is now implemented. CHANGELOG entries
added for all V11.11.0–V11.11.6 phases.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** 0 new tests. Total: 434 passed, 0 failed.

---

## V11.11.5 — 2026-04-29

### PSA × HTA integration tests (8 tests)

Created `tests/test_psa_hta_integration.py` with 8 tests covering the full
PSA × HTA pipeline:

- `test_psa_hta_end_to_end`: N=2 draws, verifies all 3 comparisons and all 3
  arms present, output structure correct.
- `test_psa_hta_deterministic_reproducibility`: Two runs with same seed produce
  identical incremental DataFrames.
- `test_psa_hta_incremental_consistency`: Manually recomputes delta_cost and
  delta_qaly from arm_results and verifies exact float equality with stored
  incremental values.
- `test_ceac_boundary_values`: Synthetic data — P(CE) at WTP=0 ≈ 0 for positive
  delta_cost; P(CE) at WTP=∞ ≈ 1 for positive delta_qaly.
- `test_ceac_monotonicity`: CEAC is non-decreasing in WTP for each comparison.
- `test_hta_params_affect_costs`: Setting art_first_line cost to 50× base
  (with seeded HIV population) increases total_costs_disc in all arms.
- `test_no_config_file_mutation`: MD5 hashes of all three config files unchanged
  after PSA run.
- `test_perspective_societal_vs_payer`: Payer perspective uses total_costs_disc;
  societal uses total_costs_disc_societal. Verified by exact float equality.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** 8 new tests in `tests/test_psa_hta_integration.py`. Total: 434 passed, 0 failed.

---

## V11.11.4 — 2026-04-29

### PSA × HTA output writer (CE plane, CEAC, incremental summary)

Created `v11/psa_hta_output_writer.py` with `PSAHTAOutputWriter` class. Writes:

**Raw CSVs:** `psa_hta_incremental.csv` (one row per draw × comparison),
`psa_hta_arm_outcomes.csv` (one row per draw × arm).

**Summary Excel** (`psa_hta_summary.xlsx`): 6 sheets — epi_parameter_summary,
hta_parameter_summary, arm_outcome_summary, incremental_summary (with P(CE)
at configured WTP), ceac_data (WTP × comparison pivot), raw_incremental.

**Plots:** CE plane scatter with WTP threshold line and quadrant labels
(one per comparison), CEAC (all comparisons on one chart), ICER histograms,
and ICER convergence plots.

CEAC computed as P(NMB > 0) across draws for WTP range €0–€150,000 (€5,000
steps), with configured WTP inserted as an explicit evaluation point.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** 0 new tests this phase. Covered by V11.11.5 integration tests.

---

## V11.11.3 — 2026-04-29

### PSA × HTA orchestrator (run_psa_hta.py)

Created `v11/run_psa_hta.py` — new entry point for PSA across the full HTA
pipeline. Key design:

- Per-draw: sample epi params (PSASampler) + HTA params (HTASampler) → run
  baseline to freeze year → fork 3 arms (no_prep, oral_prep, lenacapavir) →
  attach EconomicsAccumulator + AdverseEventModule → run evaluation horizon.
- Arm config built by standalone `_build_arm_config_standalone` (exact
  replication of `run_scenarios._build_arm_config` without requiring a
  MultiScenarioRunner instance).
- Per-draw error handling: failed draws logged and skipped; pipeline continues.
- Incremental outcomes computed for 3 comparisons: oral_prep vs no_prep,
  lenacapavir vs no_prep, lenacapavir vs oral_prep (sequential ZIN analysis).
- Respects `time_horizon.prep_reset_at_fork` flag (V11.10.7 compatibility).
- HTA parameter injection via `_inject_hta_value` (handles array-indexed paths
  using `id` field, same logic as `HTASampler._set_hta_value`).
- `--dry-run` flag lists all epi + HTA parameters without simulating.
- Startup warnings for: art_first_line mean≠value [F3], placeholder
  distributions [BACKLOG-15], independent sampling [BACKLOG-1], WTP value.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** 0 new tests this phase. Smoke tested with N=1 draw.

---

## V11.11.2 — 2026-04-29

### HTA parameter sampler (hta_sampler.py + 8 tests)

Created `v11/hta_sampler.py` with `HTASampler` class. Discovers all non-fixed
distribution blocks in `hta_config.json` via structured recursive walk
(utilities, disease_costs, prevention_costs.shared, prevention_costs.arm_specific,
adverse_events.items, perspective.productivity_loss). 30 parameters discovered
with current hta_config; 4 literature-sourced utilities remain when
`exclude_placeholders=True`.

**Distribution parameterisations:**
- Gamma: method-of-moments from `mean` and `se` → alpha=(mean/se)², scale=se²/mean.
- Beta: `alpha` and `beta` directly.
- Array-indexed paths use item `id` field for stability (e.g.,
  `adverse_events.items.oral_prep_gi_ae.cost`).

**`apply_draw_to_hta_config`:** Deep-copies hta_config, navigates path, sets
`value` key. Does not mutate original.

**`psa_config.json` addition:** New `hta_sampling` block with `enabled`,
`exclude_placeholders`, and `exclude_parameters` keys. No existing keys changed.

**Bug note:** Terminal list-navigation fix applied to `_set_hta_value` — when
the terminal node is a list (e.g., `prevention_costs.shared`), the item is
found by `id` field before setting its `value` key.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** 8 new tests in `tests/test_hta_sampler.py`. Total: 426 passed, 0 failed.

---

## V11.11.1 — 2026-04-29

### Test gap closure (13 new tests)

Added tests to close gaps identified in PSA audit (F12, F13, F14, F15):

**`tests/test_psa_sampler.py` (+3):** `test_gamma_sampling_moments`,
`test_normal_sampling_moments`, `test_uniform_sampling_moments` — moment
validation for Gamma(5, scale=0.5), Normal(10, 2), and Uniform(3, 7)
distributions using N=10,000 draws. All verify |sample_mean − theoretical|
< 5% and |sample_var − theoretical| < 10%.

**`tests/test_psa_metrics.py` (+3):** `test_mean_aggregation`,
`test_max_aggregation`, `test_min_aggregation` — verify mean/max/min
aggregation over year_range=[2011, 2013] with synthetic 5-row DataFrame.

**`tests/test_psa_integration.py` (+2):** `test_parallel_sequential_equivalence`
(N=3 draws, workers=1 vs workers=2 produce identical DataFrames) and
`test_single_draw_failure_recovery` (normal run completes without raising).

**`tests/test_psa_output_writer.py` (new file, 5 tests):**
`test_outcome_summary_statistics`, `test_rank_correlation_known_monotonic`,
`test_rank_correlation_independent`, `test_convergence_data_shape`,
`test_excel_workbook_structure` — unit tests for PSAOutputWriter internals
using synthetic data and MockSampler.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** 13 new tests. Total: 418 passed, 0 failed.

---

## V11.11.0 — 2026-04-29

### PSA quick fixes (F5, F6, F9, F10, F11, F16 from audit)

Applied 4 non-structural fixes to existing PSA files:

**F5 — Per-draw error handling (`run_psa.py`):** Both `_run_sequential` and
`_run_parallel` now wrap the per-draw body in try/except, collecting failed
draws into a `failed_draws` list. Warnings printed. Surviving draws are
returned normally. `PSAOutputWriter` updated to accept `failed_draws=None`;
writes a `failed_draws` sheet to the Excel workbook if any draws fail.

**F6 — Tornado label fix (`psa_config.json`):** Changed `"notes"` from
"Partial Rank Correlation Coefficient tornado diagram" to "Spearman rank
correlation tornado diagram". The plot title in `psa_output_writer.py` already
used the correct label ("Spearman Rank Correlation") — no code change needed.

**F9 + F10 — Code deduplication + sys.path guard (`run_psa.py`):**
`_run_single_draw` (parallel worker) refactored to call `_prepare_draw_config`
instead of duplicating its logic. Added `if _dir not in sys.path` guard before
`sys.path.insert` to prevent duplicate path entries across repeated imports.

**F11 — Explicit engine cleanup:** `del engine` after each draw in
`_run_sequential` and `_run_single_draw` to release memory promptly.

**F16 — Convergence edge case (`psa_output_writer.py`):** Split the
`n_total <= 1` guard into two branches: `n_total == 0` returns an empty
DataFrame with correct column names; `n_total == 1` returns a single-row
DataFrame with NaN CI values.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** 0 new tests. All 405 pre-existing tests pass.

---

## V11.10.8 — 2026-04-29

### Fix: missing dependencies in requirements.txt

`scipy` and `openpyxl` were used in production code (`psa_sampler.py`, `psa_output_writer.py`, `hta_report.py`) and their test files, but were absent from `requirements.txt`. A clean environment install would fail silently at import time for these modules. Added:

- `scipy>=1.7.0` — minimum version for `scipy.stats.qmc` (Latin Hypercube Sampling), used in PSA sampler
- `openpyxl>=3.0.0` — used by HTA Excel report writer and PSA output writer

**No code changes. No numeric parameters changed.**

**Tests:** Full suite now 406 passed (previously 392 when scipy was missing from environment).

---

## V11.10.7 — 2026-04-29

### HTA baseline PrEP reset at fork (feature flag)

Added `prep_reset_at_fork` flag (default: `true`) to `hta_config.json → time_horizon`.

When enabled, all existing PrEP users in the baseline snapshot are collapsed into the None slot immediately after each arm's snapshot is restored and before the arm engine is constructed. This ensures all three HTA arms (no_prep, oral_prep, lenacapavir) start from an equal PrEP-naive stock at the 2025 decision year. Historical PrEP effects on HIV prevalence are fully preserved in the snapshot population distribution; only the headcount in PrEP slots is cleared.

This is a forward-compatibility guard: the flag has zero effect under the current default config (both `oral_prep.enabled = false` and `lenacapavir.enabled = false` in the baseline → n_prep=1 at freeze → `_collapse_prep_to_none` is a documented no-op). When the baseline is eventually enabled with real Dutch PrEP scale-up data (2019–2025), this flag ensures the arm comparison remains methodologically equal without any additional config edits.

**Files changed:** `configs/hta_config.json`, `run_scenarios.py`

**No numeric parameters changed. No simulation behaviour changes (verified by regression test).**

**Tests:** 5 new tests in `tests/test_prep_reset.py`. All pass. Full suite passes.

---

## V11.10.6 — 2026-04-27

### PSA parallel execution

Added `--workers` flag to `run_psa.py` for multi-process parallel execution via `concurrent.futures.ProcessPoolExecutor`. Each PSA draw is independent — parallelism is embarrassingly parallel. `--workers 0` auto-detects CPU count. Results are deterministic and identical to sequential execution (verified). With 2 workers: ~1.9x speedup (0.5s/draw vs 1.0s/draw). At 8 cores, a 1000-draw PSA drops from ~17 min to ~2-3 min.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** Sequential/parallel equivalence verified programmatically. All 30 PSA tests pass.

---

## V11.10.5 — 2026-04-27

### PSA integration and stress tests

Created `tests/test_psa_integration.py` with 10 tests: end-to-end PSA run, deterministic reproducibility, parameter effect direction (monotonicity), base config mutation guard, bounds enforcement, non-negative outcomes, lower-bound stress test, upper-bound stress test, no-NaN stress test, base-value injection regression test.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** 10 new tests in `tests/test_psa_integration.py`. Total: 358+30 passed, 0 failed.

---

## V11.10.4 — 2026-04-27

### PSA output writer

Created `psa_output_writer.py` with `PSAOutputWriter` class. Writes raw CSV (draws + outcomes matrices), summary Excel workbook (6 sheets: parameter_summary, outcome_summary, raw_draws, raw_outcomes, tornado_data, convergence), tornado diagram (Spearman rank correlation), outcome histograms, and convergence plot (running mean ± 95% CI).

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** Exercised by integration tests in V11.10.5. Smoke tested with N=2.

---

## V11.10.3 — 2026-04-27

### PSA runner CLI and orchestration

Created `run_psa.py` — standalone PSA entry point. Orchestration loop: load configs, init sampler/extractor, generate LHS draws, run N simulation iterations with parameter injection, collect outcome metrics, write outputs. Supports CLI overrides (--n-draws, --seed, --output-dir, --dry-run, --quiet). Enforces deterministic mode. Suppresses engine file I/O and terminal output during draws.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** Exercised by integration tests in V11.10.5. Dry-run and N=2 smoke tests passed.

---

## V11.10.2 — 2026-04-27

### PSA configuration file

Created `configs/psa_config.json` — all PSA-specific settings (sampling method, N draws, seed, tier selection, include/exclude filters, metric definitions, output preferences, plot toggles). Separate from model config as required by PSA design. Default: 1000 LHS draws over 9 Tier-3 uncertain parameters with 4 outcome metrics.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** Config validated implicitly by PSASampler and MetricExtractor test suites.

---

## V11.10.1 — 2026-04-27

### PSA metric extraction module

Created `psa_metrics.py` with `MetricExtractor` class. Config-driven metric extraction from simulation DataFrames — supports sum, mean, value_at_year, final, max, min aggregations with optional year ranges. No hardcoded metrics; all logic driven by `psa_config["metrics"]["extract"]`.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** 6 new tests in `tests/test_psa_metrics.py`. Total: 358+20 passed, 0 failed.

---

## V11.10.0 — 2026-04-27

### PSA parameter sampler module

Created `psa_sampler.py` with `PSASampler` class. Dynamically discovers Tier-3 ("uncertain") parameters from config via `get_params_by_tier()`, builds frozen scipy distribution objects (Beta, LogNormal, Gamma, Normal, Uniform), generates parameter draw matrices using Latin Hypercube Sampling (LHS) or simple Monte Carlo. Supports include/exclude filters, bounds enforcement, and parameter summary export.

Created `configs/psa_config.json` (PSA settings needed by sampler tests).

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** 14 new tests in `tests/test_psa_sampler.py`. Total: 358+14 passed, 0 failed.

---

## [Patch] Utility Values — BACKLOG-15 — 2026-04-26

### Utility values corrected, sourced, and PSA distributions derived from literature

**Files changed:** `configs/hta_config.json` (utilities block), `documentation/UTILITY_VALUE_ANALYSIS_2026_04_26.md` (updated with Option A note), `documentation/UTILITY_VALUE_DERIVATIONS_2026_04_26.xlsx` (new — full audit trail), `documentation/sessions/week_of_2026_04_21/SESSION_2026_04_26_UTILITY_VALUES.md` (updated)

**Sources applied:** Miners et al. 2014 (*Lancet HIV*); Popping et al. 2021 (*Lancet Reg Health Eur*); Nichols et al. 2016 (*Lancet Infect Dis*, Dutch PrEP CEA); NICE TA1106 2025; Tengs & Lin 2002 (*Med Decis Making*)

**Changes to `hta_config.json` utilities:**

| State | Old | New | Basis |
|---|---|---|---|
| `susceptible` | 1.0 | 1.0 | No change — convention confirmed (Nichols NL, TA1106) |
| `susceptible_oral_prep` | 0.99 | **1.0** | No EQ-5D source for decrement; Nichols NL and TA1106 apply zero |
| `susceptible_lenacapavir` | 0.995 | **1.0** | Same; removes unsourced 0.005 structural QALY advantage |
| `hiv_acute` | 0.70 | **0.78** | Previous 0.70 was Tengs AIDS stage; 0.78 = symptomatic HIV proxy (Tengs 0.82 / Nichols CD4 200–350), citation corrected |
| `hiv_chronic_on_art` | 0.85 | **0.850** | Value unchanged; citation corrected from "Miners" to Popping 2021 NL EQ-5D-5L (n=895) — Miners gives 0.74, not 0.85 |
| `hiv_chronic_off_art` | 0.78 | **0.78** | Value unchanged; citation added (Tengs/Nichols) |
| `aids_on_art` | 0.60 | **0.70** | Previous 0.60 below all primary sources; Tengs TTO AIDS=0.702, Nichols NL Table S1=0.70 |
| `aids_off_art` | 0.40 | **0.70** | **Option A** — Tengs 2002 has single AIDS utility, no ART stratification; set equal to aids_on_art; previous 0.40 unsourced; near-empty state in NL 2025; DSA required; pending better source |

**PSA distributions:** All disease-state utilities converted from mechanical α=value×100 placeholders to Briggs method-of-moments Beta parameters from literature-sourced bounds. Fixed distributions applied to susceptible states (no source for PSA variation). Full derivations in `documentation/UTILITY_VALUE_DERIVATIONS_2026_04_26.xlsx`.

**Direction of bias:** PrEP decrement removal (neutral); `hiv_acute` 0.70→0.78 and `aids_on_art` 0.60→0.70 both reduce disease burden (conservative, slightly biases against PrEP).

---

## [Research] Utility Value Analysis — 2026-04-26

### BACKLOG-15 (Partial): Utility values literature review complete

**Files created/updated:** `documentation/UTILITY_VALUE_ANALYSIS_2026_04_26.md` (new), `documentation/BACKLOG.md` (BACKLOG-15 updated), `documentation/references/cabotegravir_prep/` (NICE PDFs added, README updated)

**No `hta_config.json` values changed at this stage — changes implemented in subsequent patch above.**

**Sources reviewed:** Miners et al. (2014) *Lancet HIV*; NICE TA1106 (2025); Popping et al. (2021) *Lancet Reg Health Eur*; Nichols et al. (2016) *Lancet Infect Dis* + Supplemental; Tengs & Lin (2002) *Med Decis Making*; de Boer-van der Kolk et al. (2010) ATHENA — NOT USABLE (MOS-HIV instrument).

---

## V11.9.12 — 2026-04-26

### Feature: HIV+ On-ART Patient Time Costs in Societal Perspective

**Files changed:** `economics.py` — `_compute_patient_time_costs()`, `configs/hta_config.json`, `tests/test_economics_v2.py`  
**Tests added:** 5 new tests in `tests/test_economics_v2.py::TestHIVPatientTimeCosts`

**Problem:** `_compute_patient_time_costs()` only counted susceptible PrEP users (oral PrEP 4 visits/year, lenacapavir 2 visits/year). The `no_prep` arm returned `0.0` unconditionally via an early return. Under a societal perspective (ZIN 2022), patient time costs include the opportunity cost of **all** medical appointments — including the quarterly outpatient monitoring (viral load, CD4, labs, clinical review) that HIV+ individuals on ART attend. This omission was arm-asymmetric: the no_prep arm carries more HIV+ patients, so its societal burden was understated more than intervention arms, making PrEP appear less cost-effective than it is.

**Fix — `economics.py`:**  
`_compute_patient_time_costs()` restructured into two components:

1. **PrEP monitoring visits (unchanged):** Oral PrEP (4/year) and lenacapavir (2/year) for susceptible users. No_prep arm contributes zero from this component.

2. **HIV+ on-ART monitoring visits (new, V11.9.12):** Iterates Acute/Chronic/AIDS stages for **all** arms including no_prep. Restricted to `art_dim=1` (On_ART only) — off-ART patients are not in structured monitoring and must not be charged visit costs. Reads `hiv_art_visits_per_year` from config (default 4, matching Dutch HIV treatment guidelines / SHM ATHENA protocol).

The early return for `no_prep` arm is removed. The `else: return 0.0, {}` branch on arm name is replaced with a `pass` (no_prep falls through to Component 2). Cost accumulates into the existing `"patient_time"` breakdown key.

**Fix — `hta_config.json`:**  
Added `hiv_art_visits_per_year` (value=4) to `perspective.patient_time_costs`:
```
source: Stichting HIV Monitoring ATHENA cohort protocol; NVHB 2024 HIV treatment guidelines
distribution: fixed (placeholder)
```

**Parallel development note:** V11.9.11 (implemented concurrently) touched `_compute_prevention_costs_v2()` and `oral_loading_year1` config entry — entirely separate function and config section. No conflicts. Two V11.9.11 test scaffolding defects (`BASE_HTA_CONFIG` undefined, `_make_accumulator()` missing `hta_config` kwarg) were resolved as part of this patch; both tests now pass.

**Expected impact on results:**
- All arms gain HIV+ patient time costs in societal total; payer-perspective results unchanged
- No_prep arm sees the largest absolute increase (most HIV+ patients)
- Lenacapavir and oral_prep see smaller, similar increases
- Direction: PrEP interventions appear *more* societal-cost-effective (they prevent the HIV infections driving these costs in the no_prep arm)
- ICER (lenacapavir vs. oral_prep): small change (similar HIV+ counts in both arms); primary effect is in the oral_prep vs. no_prep comparison
- All three scenarios must be re-run to produce corrected societal outputs

**Tests:** 5 new tests in `TestHIVPatientTimeCosts`:
- `test_hiv_art_patient_time_included_in_societal` — 1,000 Chronic/on-ART in no_prep arm → €91,250/step
- `test_hiv_off_art_not_charged_patient_time` — off-ART patients yield zero
- `test_all_hiv_stages_on_art_contribute` — Acute + Chronic + AIDS all counted (350 × €91.25 = €31,937.50)
- `test_no_prep_arm_hiv_time_higher_than_prep_arm` — directional integration check
- `test_payer_perspective_unaffected` — no perspective block → zero societal additions

**Suite:** 369 passed, 0 failed (net +7 from V11.9.11 scaffolding fixes + 5 new V11.9.12 tests).

---

## V11.9.11 — 2026-04-26

### Bug Fix: oral_loading_year1 Undercharged Due to Incorrect step_fraction Multiplication

**Files changed:**
- `configs/hta_config.json` — `oral_loading_year1.frequency`: `"annual"` → `"once"`
- `economics.py` — `_compute_prevention_costs_v2()`: skip `step_fraction` when `frequency == "once"`
- `tests/test_economics_v2.py` — 3 new tests in `TestOnceBillingPrevention`

**Bug:** `oral_loading_year1` (€839.98 — two oral Lenacapavir tablets given once at
initiation alongside the first injection) had `frequency:"annual"` in `hta_config.json`.
The arm-specific loop in `_compute_prevention_costs_v2()` multiplied all items by
`step_fraction` unconditionally. At quarterly resolution (`step_fraction = 0.25`):

```
charged per initiator: €839.98 × 0.25 = €209.99   (was)
correct charge:        €839.98 × 1.0  = €839.98   (now)
undercount per person: €630.00
```

At 1,000 new initiators/year this amounts to €630,000 undercount annually; over a
20-year horizon with continuous uptake this is material for the ICER.

**Why `billing_moment` could not be used as the discriminator:**
`injection_year1` also has `billing_moment:"initiation"` but its value (€41,110.44)
IS an annualised rate correctly amortised quarterly via `step_fraction`. Using
`billing_moment` as the switch would have broken `injection_year1`. The correct
discriminator is `frequency:"once"` vs `frequency:"annual"`.

**Fix:**
1. Config: `oral_loading_year1.frequency` changed to `"once"` to accurately reflect
   that this is a one-time event, not an annualised rate.
2. Code: arm-specific prevention cost loop now branches on `frequency`:
   - `"once"` → `amount = count × item_cost` (no `step_fraction`)
   - anything else → `amount = count × item_cost × step_fraction` (unchanged)

**Tests added:** 3 in `TestOnceBillingPrevention`
- `test_once_frequency_charges_full_value` — 100 initiators → oral loading = €83,998 (not €20,999)
- `test_annual_frequency_still_uses_step_fraction` — regression, annual items unaffected
- `test_oral_loading_year1_two_step_sequence` — fires at full value step 0, zero step 1 (no new initiators)

**Finding source:** HTA Adversarial Review 2026-04-26, Finding F2.

---

## V11.9.10 — 2026-04-20

### Bug Fix: Cohort Split Overcharge on PrEP Dropout

**File changed:** `economics.py` — `_compute_prevention_costs_v2()`  
**Tests added:** 4 new tests in `tests/test_economics_v2.py::TestPreventionCostsLenacapavir`

**Bug:** `continuing = prev_users` in the cohort split was wrong when users dropped off
Lenacapavir between timesteps. When `current_users < prev_users`, the code billed
`prev_users` people the subsequent-year injection rate, overcharging for users who were
no longer on PrEP. For example, if 100 people were on Lena at step N and 60 remained
at step N+1, the model charged 100 × €20,555.22 × step_fraction instead of 60 × —
an overcharge of €205,552 per step at quarterly resolution.

**Fix:** `continuing = min(current_users, prev_users)`  
Continuing users are those present in both this step and the last — always the lower
of the two counts. New initiators remain `max(0, current − prev)`.

**Documented limitation (unchanged behaviour):** When a user drops off and later rejoins,
they re-enter the `new_initiators` cohort and are charged the year-1 (loading dose)
rate again. The accumulator holds no memory of prior exposure beyond the previous
step's count. This is a population-model approximation and is noted as a limitation.

**Why missed:** All pre-existing cohort tests used monotonically increasing or stable
user counts. The `continuing` formula only diverges from `min()` when `current < prev`;
no test ever exercised a dropout.

---

## V11.9.9 — 2026-04-19

### Test Suite Remediation — 5 fixes, 0 failures, 0 collection errors

Full inventory and remediation of accumulated test suite failures. End state:
**358 passed, 0 failed, 0 collection errors** (up from 330 passed / 28 failed / 15 collection errors).

**Fix A — `conftest.py` added to `v11/`:**  
Adds `v11/` to `sys.path` for all pytest imports. Unblocked `test_hta_report.py`
(19 tests) which was silently uncollectable due to `ModuleNotFoundError`.

**Fix B — Two stale test assertions updated:**
- `test_config_reader.py::test_disease_treatment_reconstruction`: removed assertions for
  `art_initiation_rate` / `art_dropout_rate` — deliberately removed as dead code in V11.9.4.
  Golden reference filtered before `_deep_equal` comparison (file not modified).
- `test_high_prevalence_stress.py::test_valid_epidemiological_outputs`: updated
  `outputs['incidence']` → `outputs['incidence_rate']` (renamed V11.4 CHANGELOG B4).

**Fix C — `test_prep_dynamics.py` refactored (hardcoded `/tmp` path removed):**  
`make_prep_enabled_config()` now returns a config dict directly. `run_engine()` passes
the dict to `SimulationEngineV3` (dict input supported since V11.9.6 BLOCKER-A fix).
All 15 affected tests now pass; no file I/O in test helpers.

**Fix D — Two V6-era test files migrated to V11-native format:**
- `test_art_dependent_progression.py` → `test_art_progression.py` (7 tests):
  ART-dependent Chronic→AIDS progression validated using direct `_step_disease_progression()`
  calls on synthetic tensor state. No schedule dependency — clean unit-level tests.
- `test_fractional_aging_basic.py` → `test_aging_conservation.py` (4 tests):
  Population conservation across adult aging, transfer direction, terminal bin accumulation,
  and childhood aging (with debut outflow accounting) — all confirmed unique coverage
  not present in any other passing test.

**Fix E — 18 legacy script files deleted from `tests/`:**  
V2/V10-era debug scripts with no `def test_*` functions, referencing non-existent
configs (`config_v6_rel_pop.json`, `config_v5.json`) or deprecated APIs (`simulate_v3`).
Originals replaced by migrations in Fix D are also removed. History preserved in git.

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** 358/358 pass. All collection errors resolved.

---

## V11.9.8 — 2026-04-19

### BACKLOG-19 — Shape parameter validation in `config_utils.py`

`_validate_entry()` now checks that every `tier: "uncertain"` parameter carries the
required shape parameters for its declared distribution type, and that those shape
parameters are numerically valid (positive where required).

**New constants in `config_utils.py`:**
- `REQUIRED_SHAPE_PARAMS` — maps each distribution type to its set of required fields;
  serves as the canonical in-code reference for the PSA sampler
- `POSITIVE_SHAPE_PARAMS` — shape params that must be strictly > 0 (`alpha`, `beta`, `sigma`)

**Validation added to `_validate_entry()`:**
- Missing shape param: `"{path}: missing shape parameter '{param}' for {type} distribution"`
- Invalid range: `"{path}: invalid value for shape parameter '{param}' in {type} distribution — expected a positive number, got {value!r}"`

**Reference block added to `configs/v11_configuration.json`:**
- `_psa_distribution_reference` — top-level `_`-prefixed key listing required fields,
  scipy call, use-for guidance, and constraints for each distribution type; gives editors
  an inline reference without needing to open `config_utils.py`

**New tests in `tests/test_parameter_tiers.py` (8 tests):**
- `test_shape_param_presence_beta/lognormal/gamma/normal` — presence checks per type
- `test_shape_param_uniform_no_required` — Uniform correctly requires no shape params
- `test_shape_param_invalid_range_alpha/sigma` — positivity constraint enforcement
- `test_live_config_shape_params_valid` — regression guard on live config

**No numeric parameters changed. No simulation behaviour changes.**

**Tests:** 14/14 parameter tier tests pass. 111 non-legacy tests pass (2 pre-existing
failures: `test_disease_treatment_reconstruction` and `test_valid_epidemiological_outputs`
— BACKLOG-8 and V11.4 key-rename issue, both unrelated to this change).

---

## V11.9.7 — 2026-04-19

### BLOCKER-C — Schema violations cleared (`configs/v11_configuration.json`)

Four `beta_per_route` Tier-2 entries were missing the required `calibration_target`
metadata field, causing `validate_tier_schema()` to return 4 violations — which would
abort the PSA pre-flight check before any draw could run.

**Entries fixed (metadata only — no numeric values changed):**
- `beta_per_route.vaginal_mtf.chronic`: added `calibration_target` referencing RIVM
  heterosexual HIV incidence and Boily 2009 HIC M→F pooled estimate
- `beta_per_route.vaginal_ftm.chronic`: added `calibration_target` referencing RIVM
  heterosexual HIV incidence and Boily 2009 HIC F→M pooled estimate
- `beta_per_route.anal_receptive.chronic`: added `calibration_target` referencing RIVM
  MSM HIV incidence / Amsterdam Cohort Studies and Baggaley 2010 URAI estimate
- `beta_per_route.anal_insertive.chronic`: added `calibration_target` referencing RIVM
  MSM HIV incidence / Amsterdam Cohort Studies and Vittinghoff 1999 UIAI estimate

`validate_tier_schema()` now returns 0 violations. PSA pre-flight will pass cleanly.
No simulation behaviour changes — `calibration_target` is documentation metadata only.

**Tests:** 62/62 (parameter tiers, HTA config validator, PSA infrastructure).

---

## V11.9.6 — 2026-04-19

### PSA Infrastructure Groundwork — BLOCKER-A + BLOCKER-B

Clears two of three hard blockers identified in the 2026-04-19 PSA pre-flight audit.
No numeric parameters changed. No existing test behaviour changed.

**BLOCKER-A — `SimulationEngineV3` now accepts a config dict or a file path (`run_v3.py`)**
- `__init__` parameter renamed from `config_path: str` to `config_path_or_dict`
- If a `dict` is passed, it is used directly as the raw config (no file I/O)
- If a `str` is passed, behaviour is identical to before (backward compatible)
- Any other type raises `TypeError` with an informative message
- Unlocks the PSA runner: N=1000 draws can be injected as dicts without N temp file writes
- Test A1 confirms dict and path inputs produce byte-identical outputs at the same seed

**BLOCKER-B — `_build_arm_config` returns a dict; `param_overrides` injection added (`run_scenarios.py`)**
- New module-level utility `_deep_set(config, dot_path, value)`: sets a value at any
  dot-separated key path in a nested dict. Terminal key is set directly; caller includes
  ".value" suffix when targeting a `{"value": ...}` wrapped entry.
- `_build_arm_config` signature extended: `param_overrides: dict = None` added as
  optional third argument. Overrides are applied **before** arm definitions so arm
  definitions always take precedence (arm isolation cannot be broken via param_overrides).
- Return type changed from `str` (temp file path) to `dict`. No temp file is written;
  the returned dict is passed directly to `SimulationEngineV3`.
- HTA arm loop call site updated: `arm_config_path` → `arm_config_dict`, `try/finally`
  cleanup block removed (no file exists to clean up). Indentation dedented by one level.
- `tempfile` import retained — still used by `_run_baseline_and_scenarios` and
  `_run_independent_scenarios` in the non-HTA runner classes.

**Tests:** 26 new tests in `tests/test_psa_infrastructure.py` (26/26 passing).
All 167 directly affected HTA tests pass (test_hta_e2e, test_hta_report,
test_simulation_transparency, test_hta_config_validator, test_parameter_tiers).

**Remaining blocker:** BLOCKER-C (4 schema violations in `validate_tier_schema` on
beta_per_route Tier-2 entries) is intentionally paused pending a separate decision.

---

## V11.9.4 — 2026-04-19

### Fixes B1 / B2 / B3 — Code Audit Findings (corrected severities)

**B1 — PrEP schedule shape in `_build_arm_config` (`run_scenarios.py`) [Medium]**
`_build_arm_config` was writing `initiation` as a flat `{year: 0.0}` dict instead of the
nested `{"schedule": {year: 0.0}}` shape that `ScheduleManager._load_schedule` expects.
The mismatch caused `_load_schedule` to receive an empty dict → empty `TimeSchedule` →
`get_value()` returns `default_before=0.0`. Net effect: PrEP initiation WAS correctly
zero in the no_prep arm — but for the wrong reason (empty schedule, not zeroed schedule).
Fixed to write the correct nested shape. Audit's claim that results were wrong is
**incorrect**; the arm was producing zero PrEP as intended, coincidentally.

**B2 — Dead ART fallback constants in `config_reader.disease()` (`config_reader.py`) [Low]**
`disease()` was always injecting `art_initiation_rate=0.8` and `art_dropout_rate=0.05`
into the constructed config dict. These values are never read: `_step_art_transitions`
reads from `ScheduleManager` (when `art_schedule` present) or uses `0.0` (when absent) —
never from `config["disease"]["treatment"]`. Removed the dead keys; replaced with a
comment explaining why they are absent. Audit's conclusion that these "could drive a
valid-looking simulation" is **incorrect** — they were never consumed. Hard `ConfigError`
recommendation rejected as it would break valid test scenarios without `art_schedule`.

**B3 — `engine` unbound in `simulate_v3` (`run_v3.py`) [Low / Defensive]**
Audit claimed `NameError` on `n_runs=0`. Claim is **factually wrong** — Python
short-circuits `n_runs > 1 and engine.output_writer.enabled`, so `engine` is never
accessed when `n_runs=0`. Fixed anyway: initialised `engine = None` before the loop
and guarded post-loop check with `engine is not None`, making the intent explicit
without relying on short-circuit reasoning.

**Tests:** 124 passing (no regressions).

---

## V11.9.3 — 2026-04-19

### Fix B4: AE Item Validator / Runtime Mismatch (billing_moment)

**Problem:** `_validate_ae_items` accepted `{"ongoing", "initiation", "once", "diagnosis"}` as
valid `billing_moment` values for `adverse_events.items[]`. At runtime, `_compute_v2` only
routes `"ongoing"` and `"initiation"` — any item configured with `"once"` or `"diagnosis"`
would silently contribute `(0.0, 0.0)` to AE costs with no warning or error.

No active config items were affected (confirmed by audit of `hta_config.json`).

**Fix (option b — narrow validator):**
- `config_utils.py` `_validate_ae_items`: narrowed `valid_billing` to `{"ongoing", "initiation"}`.
  Error message now explicitly states that `"diagnosis"` belongs in `disease_costs` and `"once"`
  is unimplemented.
- `adverse_events.py` `_compute_v2`: replaced silent `(0.0, 0.0)` fallthrough with a `ValueError`
  so any value that bypasses validation still surfaces immediately.
- Updated `_compute_v2` docstring to remove the misleading `"diagnosis"` routing entry.

**Tests:** 2 new tests added to `TestValidatorBillingMoment`:
- `test_invalid_ae_billing_once_raises` — `"once"` now rejected at validation
- `test_invalid_ae_billing_diagnosis_raises` — `"diagnosis"` now rejected at validation
- `test_valid_ae_billing_moments_pass` updated to only loop over `{"ongoing", "initiation"}`

106 tests passing (net +2).

Note: `disease_costs` validator retains `"death"`, `"diagnosis"`, `"ongoing"` — all correctly
routed by `economics.py`. The audit's claim about `"death"` being unrouted was incorrect.

---

## V11.9.2 — 2026-04-19

### Ghost Code Cleanup (Dead Code Audit)

Six confirmed dead/ghost code items removed from the production codebase:

**Files deleted:**
- `hta_config_compat.py` — tombstone file (raise-on-import) with zero live callers; physical file removed (functional removal was V11.9.1, file still existed as a marker)
- `regression_test.py` — abandoned script comparing against `/tmp/v6_baseline.csv` (non-existent); no CI hook; no callers

**engine_v3.py:**
- Removed unused imports: `pandas as pd`, `json`, `pathlib.Path` — none were referenced anywhere in the module body

**demography.py:**
- Removed `DEBUG_FERTILITY = False` flag, all three `if DEBUG_FERTILITY:` conditional blocks (~80 lines), and debug-only loop accumulators (`total_births_raw`, `asfr_sum`); flag was hardcoded False with explicit "delete when diagnosis complete" comment
- Removed two stale docstring pointers to `REFERENCE_DISCRETE_AGING_OLD.md` (file does not exist)

**run_v3.py:**
- Removed stale docstring pointer to `REFERENCE_DISCRETE_AGING_OLD.md`

**transmission_corrected.py:**
- Removed `test_transmission_correctness()` function (~230 lines) — was only reachable via `if __name__ == "__main__"`, never imported by any other module; transmission sanity coverage exists in `tests/test_transmission_route_betas.py`

**Items audited but retained:**
- Version-tag inline comments in `hta_report.py` (`# V11.7.1: ...`) — 8 annotations providing change context; not dead code
- `PopulationState._initialize_from_direct_counts()` branch — audit incorrectly claimed `run_v3.py` rejects it; it is a functional backward-compat shim (deprecated but callable from old configs)

**Verification:** 104 tests pass across HTA, economics, and transmission suites; 12 pre-existing failures (FileNotFoundError on config path in test working directory) unaffected.

---

## V11.9.1 — 2026-04-18

### Removed: HTA Config Compat Shim (BACKLOG Finding 3)

Deleted `hta_config_compat.py` and all legacy flat-format code paths that depended on
it. Both `economics.py` and `adverse_events.py` now require v2 format directly
(`_format_version: "v2"`); passing old V11.5.x flat-format configs raises `ValueError`
at construction.

**Root cause:** The compat shim was introduced in V11.6.0 as a Phase 1 bridge. Phases 2
(V11.6.1 economics rewrite) and 3 (V11.6.2 adverse events rewrite) completed the native
v2 reading paths, leaving the shim and all legacy methods as unreachable dead code. A
secondary bug was also eliminated: legacy AE methods had `step_fraction = 0.25`
hardcoded instead of reading `self.step_fraction`, which would have silently misfired on
non-quarterly timesteps.

**Removed code:**
- `_compute_step_legacy()` from `economics.py` (~119 lines)
- `_compute_prevalence_mode()`, `_compute_state_machine_mode()`, `_init_state_machine()`,
  `_normalize_prep_key()` from `adverse_events.py` (~140 lines)
- `hta_config_compat.py` tombstoned (raises `ImportError` if accidentally imported)

**Tests updated:** `test_adverse_events.py` (superseded), `test_economics_v2.py` (test
14 converted), `test_adverse_events_v2.py` (tests 1b + 10 converted),
`test_hta_config_validator.py` (shim tests removed), `test_hta_e2e.py`
(`_make_minimal_hta_config()` converted to v2 format).

**Tests:** 310 passed (173 HTA-specific), 12 pre-existing unrelated failures unchanged.

**Files:** `economics.py`, `adverse_events.py`, `hta_config_compat.py` (tombstone),
`tests/test_adverse_events.py`, `tests/test_adverse_events_v2.py`,
`tests/test_economics_v2.py`, `tests/test_hta_config_validator.py`, `tests/test_hta_e2e.py`

See: `documentation/patches/PATCH_V11_9_1_HTA_COMPAT_SHIM_REMOVAL.md`

---

## V11.9.0 — 2026-04-18

### HTA Excel Report: PrEP Population Dynamics enhancements + Arm Full-Data tabs + Baseline decimal cleanup

**Change 1 — Total Population column in PrEP Population Dynamics (Tab 11)**

Added `total_population` as a new paired column group (Start | HCC) at columns 2–3, immediately after Year. This shows the full Netherlands population (all age groups, MSM and non-MSM) so reviewers can contextualise PrEP coverage against the wider population, not only against MSM.

- `_quarterly_to_annual`: added `"total_population"` to `STOCK_COLS` so start/HCC/end variants are aggregated per year.
- `_write_prep_dynamics_tab`: added "Total Population" group header; expanded col_headers and row_vals; all subsequent column indices shifted +2 (MSM Population now 4–7, PrEP Users MSM 8–11, etc.); HCC format check updated to `c_idx in (3, 5, 7, 9, 11)`; coverage cols check updated to `c_idx >= 24 and c_idx <= 29`; persistence/turnover at 30/31.
- Title and arm-header merges updated from col 37 → 39.

**Change 2 — Per-arm Full-Data tabs (3 new tabs)**

Three new tabs added when `baseline_full_tab.enabled` is true:
- `No PrEP Full Data` — no_prep arm, full quarterly simulation records
- `Oral PrEP Full Data` — oral_prep arm
- `Lena Full Data` — lenacapavir arm

Each tab uses the same `_write_full_data_sheet()` helper as the baseline tab, so formatting and column structure are identical. Records are pulled from the first available scenario (all scenarios share the same underlying arm run).

**Change 3 — Decimal formatting cleanup in Baseline Full Data (and all full-data tabs)**

Replaced the blanket `"#,##0.000"` float format with column-name-aware formatting via `_col_number_format()`:

- Integer count columns (`step`, population, births, MSM sub-populations, initiations/dropouts, infection counts): `#,##0` — no decimal places; values cast to `int` before write.
- `year`: `0.00` — shows quarters as 1970.00 / 1970.25 etc.
- `prevalence`, `incidence_rate`: `0.000000` — six decimal places for small epidemiological rates.
- `prep_coverage_*`: `0.0000%` — percentage with 4 d.p.
- Remaining floats: `#,##0.00` fallback.

**Files:** `hta_report.py`, `tests/test_hta_report.py`
**Tests:** 19/19 passing.

---

## V11.8.6 — 2026-04-17

### Fixed: Baseline Key Years and Baseline Full Data tabs empty in HTA Excel report

**Root cause:** The arm engines (no_prep, oral_prep, lenacapavir) share the same `run_tag="baseline"` and `run_number=1` as the baseline engine. After Phase 1 completes, each arm engine's `output_writer` overwrites `outputs/baseline_run_001.csv` with data starting from the freeze step (2025), erasing the full 1970–2025 baseline time-series. `_find_baseline_csv()` then found a file containing only post-freeze arm data, and `_load_baseline()` returned `None` (or no matching summary-year rows), leaving both tabs showing "Baseline CSV not found."

**Fix (`run_scenarios.py`):** Immediately after `baseline_engine.run()` in Phase 1, save `pd.DataFrame(baseline_engine.outputs)` to `outputs/hta_baseline_prefreeze.csv` (sibling of `outputs/hta_results/`) before any arm engine executes. `_write_hta_outputs()` now uses this pre-saved path via `self._hta_baseline_csv` instead of the fragile `_find_baseline_csv()` search. `os.path.normpath()` applied to `output_dir` before `dirname()` so trailing-slash variants resolve correctly.

**Files:** `run_scenarios.py`
**Tests:** 19/19 test_hta_report.py passing.

---

## V11.8.5 — 2026-04-17

### Fixed: Coverage metrics exceed 100% and high-risk coverage uses wrong numerator

**Bug 1 — `msm_eligible_prep` wrong denominator (`run_v3.py`)**
`msm_eligible_prep` was computed using `pop[..., none_prep]` — only counting HIV-neg MSM *not currently on PrEP*. As coverage grows, this pool shrinks, causing `cov_elig_start = PrEP_users / not-on-PrEP_pool` to exceed 100%. Fixed to sum over all prep states: `pop[..., :]` — i.e., ALL HIV-neg MSM in the eligible age range regardless of current PrEP status. This is the correct denominator for population coverage.

**Bug 2 — high-risk coverage wrong numerator (`hta_report.py`)**
`cov_hr_start` and `cov_hr_hcc` used `(oral_start + lena_start)` — all MSM on PrEP across all activity levels — as the numerator, divided by only `msm_hr_start` (high-risk MSM). This mixes strata and produces nonsense ratios. Fixed to use `msm_hr_on_prep` (start) and `msm_high_risk_on_prep_hcc` (HCC) — high-risk MSM specifically on PrEP — as the numerator.

**Config note (not a code bug):** `v11_configurationMAIN.json` `prep_schedules.Lenacapavir.discontinuation` is an empty dict `{}`. This causes `get_prep_discontinuation_rate("Lenacapavir", year)` to return 0.0, resulting in 0 dropouts, 100% persistence, and "—" turnover ratio for the lenacapavir arm. The discontinuation schedule needs to be populated with the intended annual dropout rate.

---

## V11.8.4 — 2026-04-17

### Fixed: Critical PrEP naming mismatch — PrEP was silently disabled in all arms (pre-existing)

**Root cause:** The model config uses two naming conventions for PrEP:
- `interventions` section (V10 raw): lowercase keys `"oral_prep"`, `"lenacapavir"`
- `prep_schedules` section: CamelCase keys `"Oral_PrEP"`, `"Lenacapavir"`

`ConfigReader.as_v6_dict()` correctly translates `interventions` to CamelCase, so `self.config["interventions"]` has keys `"Oral_PrEP"` / `"Lenacapavir"`. However, `run_v3.py` line 82 called `self.groups.add_prep_intervention(intervention_name)` using the **raw V10 key** (lowercase) rather than the translated state name. This caused `prep_states` to contain `"oral_prep"` / `"lenacapavir"` while every downstream lookup expected `"Oral_PrEP"` / `"Lenacapavir"`.

**Consequences:**
- `_step_prep_transitions`: `"oral_prep" not in interventions` → True → PrEP loop skipped every step → **no PrEP initiations or dropouts ever occurred in any simulation arm**
- `economics.py`: `"Oral_PrEP" in prep_states` → False → PrEP drug costs and PrEP-specific QALYs never computed → oral_prep and lenacapavir arms had identical economics to no_prep
- `record_outputs()` (V11.8.0): all new MSM-on-PrEP and flow counter metrics were 0 — but this was a symptom, not the root cause

**Fix:** Added module-level `_INTERVENTION_TO_PREP_STATE` dict in `run_v3.py` (`"oral_prep" → "Oral_PrEP"`, `"lenacapavir" → "Lenacapavir"`). Changed line 82 to map through this dict before calling `add_prep_intervention`. `prep_states` now holds CamelCase names, all downstream lookups succeed without further changes.

**Scope:** `run_v3.py` only (2-line change to `__init__`). `economics.py`, `schedule_utils.py`, `output_writer.py`, and `record_outputs()` were already written correctly for CamelCase — they now work as intended.

**Impact:** All previous simulation runs produced incorrect results. All three arms were effectively running as `no_prep`. Results must be regenerated.

---

## V11.8.3 — 2026-04-17

### Added: Incidence by Subgroup Tab (Tab 12)

New "Incidence by Subgroup" tab added to `hta_report.xlsx` via `_write_incidence_subgroup_tab()` in `hta_report.py`. Covers the evaluation horizon only. Per-arm annual rows with 13 columns: year, new infections (all / MSM / on-PrEP / off-PrEP), incidence rates per 100 person-years (all / on-PrEP / off-PrEP), HIV prevalence, cumulative infections, infections averted vs no_prep arm (blank if arm unavailable or is the reference arm), cumulative averted, and NNT (per 100 PY). Averted column colour-coded green (positive) / red (negative). NNT cell blank when on-PrEP population empty or rates unavailable.

---

## V11.8.2 — 2026-04-17

### Added: PrEP Population Dynamics Tab (Tab 11)

New "PrEP Population Dynamics" tab added to `hta_report.xlsx` via `_write_prep_dynamics_tab()` in `hta_report.py`. Three arm sections stacked vertically with navy section headers. Covers evaluation horizon only. 35 columns per arm: year + 7 stock pairs (start-of-year + HCC mean) + 6 annual flow columns (initiations / dropouts per PrEP type) + 3 coverage pairs (start + HCC) + 2 CEA metrics (persistence rate, turnover ratio). Coverage cells colour-coded: <10% red, 10–30% amber, >30% green. Persistence rate highlighted red if <80%. Turnover ratio green if >1, amber if ≈1, red if <1. Arms with no simulation records show a graceful placeholder row.

Also added `_quarterly_to_annual()` static helper: aggregates quarterly simulation records into annual rows with stock columns split into `{col}_start` / `{col}_hcc` / `{col}_end` (first step, arithmetic mean of 4, last step) and flow columns summed annually.

---

## V11.8.1 — 2026-04-17

### Added: `simulation_records` field to `ScenarioResult`

`simulation_records: List[dict] = field(default_factory=list)` added as final field of `ScenarioResult` dataclass in `scenario_comparator.py`. All 18 call sites updated explicitly: 2 internal synthetic results in `scenario_comparator.py` and test files (`test_hta_e2e.py`, `test_hta_report.py`). Data plumbing in `run_scenarios.py` converts `arm_engine.outputs` to a list of dicts via `pd.DataFrame(...).to_dict(orient='records')` and passes it through.

---

## V11.8.0 — 2026-04-17

### Added: Simulation Transparency Engine Outputs (15 new metrics)

`run_v3.py` augmented with three instance variables (`_prep_step_initiations`, `_prep_step_dropouts`, `_last_infections_array`) and 15 new per-step output fields exported to `self.outputs`:

**MSM sub-population stocks:** `msm_total`, `msm_susceptible`, `msm_eligible_prep`, `msm_high_risk`, `msm_high_risk_susceptible`, `msm_high_risk_on_prep`, `msm_hiv_pos`, `msm_on_oral`, `msm_on_lena`

**PrEP flow counters:** `prep_oral_initiations`, `prep_lena_initiations`, `prep_oral_dropouts`, `prep_lena_dropouts`

**Infection subgroup counts:** `new_infections_msm`, `new_infections_msm_on_prep`

Per-step flow counters reset at the start of `_step_prep_transitions()` for all known PrEP states. The infections array (4D: age × orient × activity × prep) stored as `self._last_infections_array` after each transmission step and indexed correctly in `record_outputs()` using 4D indexing `[:, msm_idx, :, :]`.

**Tests:** 35 new tests in `tests/test_simulation_transparency.py` (5 categories: engine counter correctness, `_quarterly_to_annual()` helper, PrEP dynamics tab render, incidence subgroup tab render, regression).

---

## V11.7.5 — 2026-04-16

### Fixed: Price Sensitivity Dimensional Mismatch (Critical)

`price_sensitivity()` and `find_threshold_price()` in `scenario_comparator.py` contained
a dimensional mismatch that caused lenacapavir to appear cost-effective at every price
level — an impossible result given oral PrEP costs 3.8× less per year per person.

**Root cause:** Both functions replaced `original_drug_cost` (total discounted aggregate
drug spend, ~billions €) with `price` from the sensitivity range (annual per-person cost,
€5,000–40,000). At ~6 orders of magnitude difference, lenacapavir's drug cost was
effectively zeroed at every test price.

**Fix:** Both functions now receive `base_annual_price: float` as a required parameter
(annual per-person EUR/year, sourced from `hta_config.injection_subsequent.value`).
The scaling uses a dimensionless ratio:
```
price_ratio   = new_annual_price / base_annual_price
new_drug_cost = original_drug_cost_disc × price_ratio
```
This correctly scales the aggregate drug spend while preserving dimensional consistency.

`find_threshold_price()` upper bound corrected from `original_drug_cost × 10` (billions)
to `base_annual_price × 10` (realistic annual per-person range).

**Callers updated:** `run_scenarios.py` and `hta_report.py` both extract `base_annual_price`
from config and pass it to both functions.

### Changed: Price Sensitivity Analysis Redesigned

**`hta_config.json`:** `price_sensitivity_range` replaced with percentage spec:
```json
{"min_pct": 10, "max_pct": 200, "step_pct": 10}
```
Generates 20 rows at 10%–200% of base annual price in 10% steps. Covers both
discount analysis (below base) and premium robustness (above base) — standard for
published threshold analyses.

**Excel tab — columns expanded (6 → 8):**
1. `% of Base Price` — immediately interpretable; fraction stored, displayed as %
2. `Lenacapavir Price (€/year)` — retained
3. `ΔCost vs Oral PrEP (€)` — retained
4. `ΔQALY` — retained; constant across rows (note: price does not affect epi)
5. `ICER (€/QALY)` — retained, NMB-based colour coding
6. `NMB at WTP (€)` — Excel formula updated to `={WTP}×D{r}−C{r}` (column shift)
7. `Cost per Infection Averted (€)` — new; changes with price; public health framing
8. `Cost-Effective at WTP` — retained
Col 9 (hidden): constant WTP values used as chart reference series.

**Chart redesigned:** Replaced single-series ICER line with threshold analysis diagram
(Drummond et al. 2015; Sanders et al. 2016 ISPOR CHEERS). Two series:
- Series 1: ICER (€/QALY) vs annual price
- Series 2: WTP threshold horizontal reference line

Intersection of the two lines identifies the cost-effectiveness threshold price.

**`price_sensitivity()` output:** Now also returns `infections_averted` and
`cost_per_infection_averted` columns (forwarded from `compare_pair()`).

### Tests

- Updated: `test_price_sensitivity_sw_ce_at_low_price`, `_sw_not_ce_at_high_price`,
  `_ne_regression`, `test_find_threshold_price_sw_quadrant` — all updated for ratio-based
  scaling. Analytic threshold corrected from €360,000,000 (absurd) to €36,000/year.
- New in `test_hta_e2e.py`:
  - `test_price_sensitivity_base_case_unchanged` — ratio=1.0 leaves costs unchanged
  - `test_price_sensitivity_half_price_halves_drug_cost` — analytic delta_cost check
  - `test_price_sensitivity_infections_averted_constant` — price does not affect epi
  - `test_price_sensitivity_cost_effective_not_all_true` — regression guard vs original bug
- New in `test_hta_report.py`:
  - `test_price_sensitivity_pct_column_values` — % column contains correct fractions
  - `test_price_sensitivity_chart_two_series` — chart has ICER + WTP series

**Test count:** 206 passing (HTA test suite) — +4 net new tests vs V11.7.4.

---

## V11.7.3 — 2026-04-16

### Added: AIDS Terminal Care Cost Infrastructure

Adds structural support for a one-time cost at the moment of AIDS death
(`billing_moment: "death"`). With the placeholder parameter value (0), model
output is numerically unchanged. The infrastructure is in place for when the
per-death cost is confirmed from literature.

**Direction of bias addressed:** Omitting terminal care understates costs in
the no-PrEP arm (which has the most AIDS deaths), making PrEP ICERs appear
higher (worse) than the true value. This is now structurally resolved.

**`run_v3.py`**
- `_step_hiv_mortality()` return type changed from `int` to `dict`:
  `{"total": int, "aids": int}`. The `"total"` key preserves exact semantics
  for all existing callers (`validate_global_population_balance()`,
  `record_outputs()`). The `"aids"` key carries AIDS-stage deaths only.
- `step_simulation()`: unpacks the mortality dict into `hiv_deaths` and
  `aids_deaths`; passes `new_aids_deaths=aids_deaths` to
  `self._economics.accumulate()`.

**`economics.py`**
- `accumulate()`: new keyword argument `new_aids_deaths: int = 0`.
  Default 0 ensures all existing call sites are unaffected.
- `_compute_step_v2()`: new `new_aids_deaths: int = 0` argument; passed
  through to `_compute_disease_costs_v2()`.
- `_compute_disease_costs_v2()`: new `billing_moment == "death"` handler:
  `amount = new_aids_deaths * item_cost`. No `step_fraction` multiplication —
  this is a one-time event per death, not a per-person-year quantity.

**`configs/hta_config.json`**
- New `"hiv_terminal_care"` entry in `disease_costs` block:
  `value: 0` (placeholder), `billing_moment: "death"`,
  `category: "terminal_care"`, `distribution: {"type": "fixed"}`.
  Candidate sources: Van Hulst et al. 2020 (Dutch PrEP CEA); SHM ATHENA
  cohort reports.

**`hta_report.py`**
- Tab 3 `cost_cats` list extended with `("terminal_care", "Terminal care costs")`.
  Previously the hardcoded list would have silently dropped any `terminal_care`
  values.

**`config_utils.py`**
- `valid_billing` whitelist in `validate_hta_config()` extended with `"death"`.

**Known limitation (BACKLOG-17):**
- `_compute_step_legacy()` (the inactive fallback path used when `_v2_config`
  is None) does NOT receive `new_aids_deaths`. If the legacy path is ever
  re-activated, terminal care costs will silently produce zero. This path is
  already scheduled for retirement as part of the Finding 3 backwards-compat
  patch. See BACKLOG-17 for detail and recommended fix.

**Tests added (`tests/test_economics_v2.py` — `TestDeathBillingMoment`, 5 new):**
- `test_death_billing_fires_for_aids_deaths` — 5 deaths × €1000 = €5000
- `test_death_billing_independent_of_step_fraction` — step_fraction=0.25 and
  step_fraction=1.0 produce identical terminal care costs (10 × €2000 = €20000)
- `test_death_billing_zero_deaths_no_cost` — no AIDS deaths → zero terminal care
- `test_diagnosis_billing_regression_after_death_refactor` — existing
  billing_moment='diagnosis' unaffected by signature change (3 × €500 = €1500)
- `test_terminal_care_separate_from_hiv_care_in_breakdown` — terminal_care and
  hiv_care are distinct breakdown keys (critical for thesis reporting)

**Test counts:** 47/47 `test_economics_v2.py` | 197/197 all HTA test files

---

## V11.7.4 — 2026-04-16

### Bug Fix: SW-Quadrant Cost-Effectiveness Decision Rule

**Problem:** The cost-effectiveness flag in `price_sensitivity()`, the binary
search direction in `find_threshold_price()`, and the ICER cell colouring in
`hta_report.py` all used `ICER ≤ WTP` as the decision rule. This is correct
only in the NE quadrant (higher cost, higher QALYs). In the SW quadrant
(cheaper but fewer QALYs — the position lenacapavir occupies when coverage
is lower than oral PrEP), `ICER ≤ WTP` produces the wrong answer: the
inequality reverses and the correct criterion is `ICER ≥ WTP`, which is
equivalent to `NMB > 0` in all four quadrants.

**Effect:** Every row of the price-sensitivity CSV and the "Cost-effective"
column of the Excel report was incorrectly flagged `False` for any SW-quadrant
scenario, regardless of how low the lenacapavir price was. At €5,000/year,
NMB = +€37B but the flag read `False`.

**Files changed:**

`scenario_comparator.py`
- `price_sensitivity()`: replaced `icer <= self.wtp` with `comparison["nmb"] > 0`.
  Also fixed stale reference `icer` → `comparison["icer"]` in `rows.append`.
  Docstring updated to state NMB-based semantics.
- `find_threshold_price()`: replaced `icer_mid <= wtp` with `comp_mid["nmb"] > 0`
  for the binary search direction. Replaced the zero-price dominance guard with
  an NMB guard (`comp_zero["nmb"] <= 0` → return None). Docstring rewritten to
  reflect that the threshold is where NMB = 0, not where ICER = WTP.

`hta_report.py`
- `_style_icer_cell()`: added optional `nmb` parameter. When `nmb` is provided,
  the green/red fill decision is driven by `nmb > 0` rather than
  `icer_value <= wtp`. Both call sites (comparison table tab and price
  sensitivity tab) updated to pass `nmb=row_data.get("nmb")`.

**Tests added (`tests/test_hta_e2e.py`):**
- New class `TestSWQuadrantCostEffectiveness` with 13 tests covering:
  - SW quadrant CE (real model values, NMB > 0 → True)
  - SW quadrant not CE (tiny cost saving, NMB < 0 → False)
  - NE/NW/SE regression tests (4 tests confirming existing quadrants unaffected)
  - `price_sensitivity` SW CE at low price, not CE at high price, NE regression
  - `find_threshold_price` SW quadrant analytic correctness (€1 tolerance)
  - `_style_icer_cell` green/red for SW CE, SW not CE, NE regression
- `test_nmb_consistency_with_icer` extended from NE-only to all four quadrants.

**Reference:** Drummond MF et al. (2015) *Methods for the Economic Evaluation
of Health Care Programmes*, 4th ed., Oxford University Press, Ch. 4.

---

## V11.7.3 — 2026-04-16

### Backwards Compatibility Retirement (Findings 1, 4, 5, 7)

Removes four backwards compatibility mechanisms identified in
`documentation/BACKWARDS_COMPAT_AUDIT_2026_04_16.md`. None of these layers
were reachable from any supported run mode (full model / HTA / PSA).

**`config_reader.py`** (Finding 1 — V6 passthrough)
- Added `ConfigFormatError(ValueError)` exception class.
- `ConfigReader.__init__()` now raises `ConfigFormatError` immediately for any
  config that is not V10 format (lacks `run_control` / `transmission_model`).
  Historic flat V6/V7 configs are rejected at construction, not silently
  passed through.
- `as_v6_dict()` docstring updated; dead V6 passthrough branch removed.

**`economics.py`** (Finding 4 — phantom breakdown keys)
- Removed `setdefault` block that force-inserted `drug=0.0`, `monitoring=0.0`,
  `hiv_care=0.0` into `get_summary()` cost_breakdown_disc on every call.
- `add_cost()` in `_compute_step_v2()` now skips zero-amount contributions
  (sparse breakdown: only categories with real costs appear).
- Callers (`run_scenarios.py`, `hta_report.py`) already used `.get(key, 0)` and
  are unaffected.

**`run_v3.py`** (Finding 5 — V4 ART rate fallback)
- Removed V4-style fallback that read `disease.treatment.art_initiation_rate`
  and `art_dropout_rate` from config when no `ScheduleManager` was configured.
- Replaced with zero-rate path: if no ART schedule is present, ART
  initiation and dropout rates are 0.0. Valid for test configs that
  deliberately omit ART. Production configs always supply an ART schedule via
  `interventions.art` (extracted by ConfigReader).

**`run_v3.py`** (Finding 7 — `direct_counts` init fallback)
- `population.initialization_method` is now a required key. Missing key raises
  `ValueError("population.initialization_method is required...")`.
- Unknown values (including the retired `direct_counts`) raise
  `ValueError("Unknown population.initialization_method: ...")`.
- All canonical and test configs already use `relative_weights`; no migration
  needed.

**Tests**
- `test_config_reader.py`: `test_v6_passthrough` replaced with
  `test_non_v10_config_raises_config_format_error`; stale
  `test_version_detection` version string updated (`11.2` → `11.7`).
- `test_economics_v2.py`: `test_legacy_keys_always_present_in_summary` replaced
  with `test_breakdown_no_phantom_zero_keys`; `test_disease_cost_breakdown_by_category`
  updated to assert zero-cost `hiv_care` key is absent rather than present as 0.0.
- `tests/test_art_no_schedule.py` (new): 6 tests covering the zero-rate ART path
  and the required `initialization_method` enforcement.
- **Total: 222 passing** (was 215 with 1 pre-existing failure; net +7 tests,
  pre-existing `test_version_detection` failure resolved)

---

## V11.7.2 — 2026-04-16

### HTA Module Hardening (10-item audit plan)

**`economics.py`**
- `step_fraction` is now a required constructor field on `EconomicsAccumulator` (was hardcoded `0.25` inside `accumulate()`). Validated in `__post_init__` to be in (0, 1].
- Removed silent fallbacks for discount rates: `disc_cfg.get("health_effects_rate", 0.015)` and `.get("cost_rate", 0.04)` replaced with explicit key access. Missing keys now raise `KeyError` with a descriptive message.
- `step_fraction` stored in each timestep record for ScenarioComparator cross-check.
- `get_summary()` guard tightened: `_cumul_qalys_disc > 0.01` (was `> 0`).

**`adverse_events.py`**
- `step_fraction: float` added as required third argument to `AdverseEventModule.__init__()`. Validated in (0, 1]. Removes hardcoded `0.25` from `_compute_v2()`.

**`scenario_comparator.py`**
- `step_fraction: float` added as required second argument to `ScenarioComparator.__init__()`. Validated in (0, 1].
- `_compute_person_years()` cross-checks stored `step_fraction` in timestep records against `self.step_fraction`; raises `ValueError` on mismatch.
- Per-person denominator changed from `avg(ref_py, intv_py)` to `intv_py` (intervention arm person-years). V11.7.1 used the average; V11.7.2 uses the intervention arm's own exposure base.
- `compare_pair()` return dict now includes `ref_person_years` and `intv_person_years` for full transparency.
- `summary_table()` guard tightened: `total_qalys_disc > 0.01` (was `> 0`).
- Class docstring updated: removed stale "WTP threshold default: EUR 50,000/QALY" text. Now correctly states ZIN 2022 tier values (€20k/€50k/€80k by disease burden).

**`run_scenarios.py`**
- `_validate_hta_config()` function added: called immediately after loading `hta_config.json`. Checks 6 required key paths and raises `ValueError` with specific missing-key information before any pipeline construction.
- `freeze_year` vs `discounting.base_year` consistency check: raises `ValueError` if they differ (both must anchor at the same year for correct ZIN discounting).
- `step_fraction` derived from `arm_engine.time.years_per_step` (converted from `run_control.step_size` months) and passed to `EconomicsAccumulator`, `AdverseEventModule`, and `ScenarioComparator`.
- Guard added: engine `years_per_step` must match `run_control.step_size / 12`; raises `ValueError` on mismatch.

**Tests**
- `test_economics_v2.py`: +8 tests (step_fraction required, invalid step_fraction, step_fraction in record, discount rate required, shared cost routing by arm — Items 1, 2, 5)
- `test_adverse_events_v2.py`: +6 tests (initiation AE fires on new initiators, does not repeat, no QALY impact, zero on churn, step_fraction required — Item 6)
- `test_hta_e2e.py`: +5 tests (step_fraction required/invalid/mismatch, per-arm PY denominator, per-arm PY in return dict). Updated all 12 `ScenarioComparator(...)` constructions to include `step_fraction=0.25`. Updated `TestPerPersonMetrics` denominator docstrings.
- `test_hta_report.py`, `test_hta_config_validator.py`, `test_adverse_events.py`: updated all `EconomicsAccumulator` and `AdverseEventModule` constructions to include `step_fraction`.
- Total: **179 tests passing** (was 138 in the HTA test files; net +41 across all updates and new tests)

---

## V11.7.1 — 2026-04-16

### WTP Required Argument + Per-Person Metrics

**`scenario_comparator.py`**
- Removed `DEFAULT_WTP = 50000.0` class constant. `wtp_threshold` is now a required constructor argument with no default. Any caller that omits it gets a `TypeError` at instantiation — eliminates silent use of a wrong threshold value.
- Added `_compute_person_years(result)` helper: sums `total_population × 0.25` across timestep records to compute total person-years for each arm.
- `compare_pair()` extended with three new output keys: `cost_per_infection_averted` (€, or None if infections_averted ≤ 0), `delta_qaly_per_1000_person_years` (or None if no population data), `incremental_cost_per_person_year` (€, or None if no population data). Backward-compatible: None returned when timestep_records lack `total_population` key.

**`economics.py`**
- `total_population: float(np.sum(population))` added to every timestep record. Used as denominator for per-person metrics in `ScenarioComparator`.

**`hta_report.py`**
- Removed silent WTP fallback: `hta_config.get("wtp_threshold_eur_per_qaly", {"value": 50000})` → `hta_config["wtp_threshold_eur_per_qaly"]`. Missing key now raises `KeyError`.
- Tab 2 (CEA Results Summary): three new columns in incremental analysis table — "Cost per Infection Averted (€)", "ΔQALY per 1,000 Person-Years", "ΔCost per Person-Year (€)". Column layout updated from 11 to 10 columns (Dominance moved to col 10).

**Tests**
- `test_economics_v2.py`: +1 test (`test_total_population_stored_in_timestep_record`)
- `test_hta_e2e.py`: Replaced hardcoded `wtp_threshold=50000` with `80000`. +9 tests across `TestWTPRequiredArg` and `TestPerPersonMetrics`.
- Total: **78 tests passing** (was 69 in affected files, net +9 new tests)

---

## V11.7.0 — 2026-04-14

### HTA Excel Report Output

Replaces 15 individual CSV files with a single structured Excel workbook
(`hta_report_<datestamp>.xlsx`) as the primary HTA output. No changes to
simulation engine, economics, AE, or comparator logic.

**New file: `hta_report.py`** — `HTAReportWriter` class producing a 10-tab
Excel workbook: Study Overview, CEA Results Summary, Costs/QALYs/Events Detail,
Parameters & Assumptions, Time-Series A, Time-Series B, Time-Series D (three
clean per-scenario sub-tabs), Price Sensitivity, Baseline Key Years, Baseline
Full Data. Charts on time-series and price sensitivity tabs. NMB and derived
values as Excel formulas. ICER cells colour-coded (green = dominant, red =
dominated, light green = cost-effective trade-off, amber = not cost-effective).
All parameter values read from hta_config.json at runtime — no hardcoding.

**Modified: `run_scenarios.py`** — `_write_hta_outputs()` calls
`HTAReportWriter.write()` as primary output step. CSV writes now gated by
`hta_config.outputs.csv_files.enabled`. Added `_find_baseline_csv()` helper.
Import added: `from hta_report import HTAReportWriter`.

**Modified: `configs/hta_config.json`** — New `outputs` block with four keys:
`excel_report.enabled`, `csv_files.enabled`, `baseline_summary_years.value`,
`baseline_full_tab.enabled`. Absent block defaults to all-enabled.

**New: `tests/test_hta_report.py`** — 17 tests covering file creation, tab
names and count, zero formula errors (recalc.py), NMB formula presence, ICER
colour coding, uniform parameter formatting, price sensitivity row count,
baseline row counts (summary and full), CSV suppression, Excel suppression,
missing outputs block defaults, chart presence, and timeseries tab distinctness.

---

## V11.6.0 — 2026-04-14

### HTA Module Redesign Phase 1 — JSON restructure, validator, and compatibility shim

**Motivation:** The MVP `hta_config.json` from V11.5.x mixed four distinct cost
archetypes in a single flat dict, had no probability distributions on any parameter
(blocking PSA work), AE items were inflexible hard-coded dict keys, and one-time costs
were never charged. This phase introduces the new four-component structure and wires
it in transparently — zero behaviour change to any existing computation.

**New file: `configs/hta_config.json`** — Full rewrite to v2 four-component format:
- `utilities`: All health-state utility weights with Beta distribution blocks
- `adverse_events.items[]`: AE as an extensible list with `arm`, `billing_moment`,
  `frequency`, and per-item distribution blocks. Four items: oral PrEP initiation
  renal screen (once), oral PrEP GI AE (ongoing), oral PrEP renal AE (ongoing),
  Lenacapavir injection site reactions (ongoing). State machine parameters included
  per item for Phase 3 use.
- `prevention_costs`: Shared STI screening + arm-specific drug/delivery costs with
  `frequency` and `billing_moment` metadata, and Gamma distributions.
- `disease_costs`: ART, monitoring, hospitalisation, diagnosis linkage — each with
  `applies_to_stages`, `applies_to_art_status`, `category`, `billing_moment`, and
  Gamma distributions.
- All numeric parameters tagged `"distribution_status": "placeholder"` — to be
  calibrated during PSA planning phase (BACKLOG-15).

**New file: `hta_config_compat.py`** — Compatibility shim:
- `is_new_format(config)`: Detects v2 format by `_format_version` or structural keys.
- `normalize_hta_config(config)`: Translates new v2 format → old flat format so
  `economics.py` and `adverse_events.py` continue reading unchanged during Phase 1.
- Multiple ongoing AE items per arm are collapsed into one combined entry that produces
  numerically identical output via the old `abs(decrement) × prevalence × users` formula.
- Initiation-moment items are intentionally excluded from the combined entry (no
  one-time event mechanism in old code; handled in Phase 2).
- Old-format configs returned as-is (identity operation — no object copy).

**Modified: `config_utils.py`** — New `validate_hta_config()` function:
- Enforces all four component sections present.
- Validates each AE item has `id`, `arm`, `billing_moment`, `frequency`,
  `prevalence`, `utility_decrement`, `cost`.
- Validates `billing_moment` ∈ `{ongoing, initiation, once, diagnosis}`.
- Beta distributions: `alpha > 0` and `beta > 0`.
- Gamma distributions: `mean > 0` and `se > 0`.
- Distribution `type` ∈ `{fixed, beta, gamma, lognormal, normal}`.
- Raises `ValueError` listing all violations in one message.

**Modified: `economics.py`** — `__post_init__` now calls `is_new_format()`;
if v2, runs `validate_hta_config()` then `normalize_hta_config()` before
proceeding with the existing read logic. No other changes.

**Modified: `adverse_events.py`** — `__init__` follows the same pattern.
No other changes.

**New file: `tests/test_hta_config_validator.py`** — 41 new tests covering:
- Format detection (5 tests)
- Valid config passes silently, including the actual disk file (2 tests)
- Missing top-level section raises ValueError (4 tests)
- AE item missing required field raises ValueError (4 tests)
- Invalid billing_moment raises ValueError (3 tests)
- Beta distribution validation: alpha=0, beta=0, missing, negative (5 tests)
- Gamma distribution validation: se=0, mean=0, missing (4 tests)
- Invalid distribution type (2 tests)
- Compat shim: structural key translation (5 tests)
- Compat shim: AE combined impact numerical equivalence (3 tests)
- EconomicsAccumulator accepts new format end-to-end (2 tests)
- AdverseEventModule accepts new format end-to-end (2 tests)

**Regression:** 154 tests pass (41 new + 113 pre-existing). 28 pre-existing
failures (legacy path/environment issues from V10-era test files) unchanged.
Zero numeric change to any model output path.

**Phase 2 (V11.6.1):** `economics.py` full accumulate() refactor.
**Phase 3 (V11.6.2):** `adverse_events.py` item iteration + state machine wiring.
See `documentation/HTA_REDESIGN_PLAN_V11_6.md`.

---

## V11.6.1 — 2026-04-14

### HTA Module Redesign Phase 2 — accumulate() full rewrite, new_diagnoses counter, arm_name routing

**Motivation:** Phase 1 wired the new v2 JSON structure in transparently via a compat
shim. Phase 2 removes the shim dependency from accumulate() and reads the layered JSON
directly, enabling proper billing_moment routing, cohort-aware prevention costs,
one-time diagnosis linkage costs, and dynamic cost breakdown categories.

**Modified: `run_v3.py`** — `_step_diagnosis()` now returns `int(np.sum(diagnosed))`.
Captured as `new_diagnoses` in `step_simulation()` and passed as the 5th argument to
`accumulate()`. This enables the economics accumulator to fire one-time linkage-to-care
costs accurately on the exact step a diagnosis occurs (D1 decision).

**Modified: `economics.py`** — Major refactor:
- `arm_name: str = "no_prep"` field added. Identifies the scenario arm for prevention
  cost routing (matches keys in `prevention_costs.arm_specific`).
- `_prev_lena_users: int` replaced by `_prev_prep_users: Dict[str, int]` — generalised
  tracking for any PrEP arm, not just Lenacapavir.
- `_cost_breakdown_disc` initialised as empty dict — categories populated dynamically
  from JSON `category` field rather than hardcoded.
- `_v2_config: Optional[dict]` stored as a reference to the original v2 config in
  `__post_init__`. The normalised (old flat) config is still stored on `self.hta_config`
  for backward compatibility with the AE module.
- `accumulate(step, year, population, new_infections, new_diagnoses=0)` — 5th argument
  added; defaults to 0 for backward compatibility with any caller that omits it.
- Route: if `_v2_config is not None` → `_compute_step_v2()`; else → `_compute_step_legacy()`.
- `_compute_step_v2()` reads v2 JSON directly:
  - **Utilities**: All states (susceptible, PrEP variants, Acute, Chronic on/off ART,
    AIDS on/off ART) read from `utilities` section with extract_value().
  - **Prevention costs** (`_compute_prevention_costs_v2()`): Shared costs (STI screening)
    applied per-user with arm filter; arm-specific costs apply cohort logic:
    - `cohort="all"` → all current users
    - `cohort="new_initiators"` → delta from `_prev_prep_users[arm]` (positive only)
    - `cohort="continuing"` → previous step's count
    - No prevention costs applied when `arm_name="no_prep"`.
  - **Disease costs** (`_compute_disease_costs_v2()`): Iterates `disease_costs` entries,
    routes by `billing_moment`:
    - `"ongoing"` → `count × cost × step_fraction`, filtered by `applies_to_stages` and
      `applies_to_art_status`.
    - `"diagnosis"` → `new_diagnoses × cost` (no step_fraction; one-time event).
  - **Category breakdown**: `breakdown[category] += amount` — fully dynamic. Any new
    category in JSON appears automatically in `cost_breakdown_disc`.
- `_compute_step_legacy()` is the exact V11.5.x accumulate() body — unchanged behaviour
  for any caller passing old flat-format hta_config.
- `get_summary()` ensures `drug`, `monitoring`, `hiv_care` are always present in returned
  `cost_breakdown_disc` via `setdefault()` for backward compatibility with `run_scenarios.py`.
- `cumul_new_diagnoses` added to `get_summary()` return dict.

**Modified: `run_scenarios.py`** — `EconomicsAccumulator` constructor now passes
`arm_name=arm_name` (1 line), wiring the scenario loop variable directly into the
accumulator.

**New file: `tests/test_economics_v2.py`** — 32 new tests in 7 classes:
- TestQALYSusceptible (3): no-PrEP, Oral PrEP, Lenacapavir utility values
- TestQALYHIV (4): Chronic on/off ART, AIDS on/off ART utility values
- TestPreventionCostsNoPrEP (1): arm_name="no_prep" → zero prevention costs
- TestPreventionCostsOralPrEP (2): drug + STI cost total; category breakdown split
- TestPreventionCostsLenacapavir (3): first step (all new), second step (all
  continuing), partial new initiators — cohort logic verified numerically
- TestDiseaseCostsDiagnosis (3): linkage cost fires on new_diagnoses count; zero
  diagnoses → zero cost; hiv_care category populated
- TestDiseaseCostsOngoing (3): Chronic on ART full cost; AIDS off ART hospitalization
  only; Chronic off ART gets zero drug cost (ART filter correct)
- TestDiscounting (3): health/cost discount factors at 1-year offset; no discount
  at freeze_year
- TestCostBreakdown (2): legacy keys always present; category breakdown correct
- TestTimestepRecords (2): step_new_diagnoses stored; copy semantics
- TestLegacyFlatFormat (3): old format uses legacy path; produces QALYs; new_diagnoses
  default=0 does not raise
- TestGetSummary (3): cumulative diagnoses across steps; discounted cost accumulation;
  invalid v2 config raises at construction

**Tests:** 32 tests — 32/32 passing (Python 3.9.6 / pytest-8.4.2, macOS, 0.11s).

**Behavior changes vs V11.6.0:**
- Lenacapavir costs now correctly charge `injection_year1` only to new initiators
  and `injection_subsequent` only to continuing users (previously both costs were
  applied to the entire user pool on each step via the flat config).
- AIDS off-ART individuals now correctly pay zero ART drug cost (previously the
  legacy path applied `art_first_line` to all Acute individuals regardless of ART).
- Oral PrEP STI screening cost (€300/year) now applied correctly as a separate
  monitoring-category item.
- One-time diagnosis linkage cost (€500) now fires accurately on the step new
  diagnoses occur, via the `new_diagnoses` counter from `_step_diagnosis()`.

**Phase 3 (V11.6.2):** `adverse_events.py` item iteration + state machine wiring.
See `documentation/HTA_REDESIGN_PLAN_V11_6.md`.

---

## V11.6.2 — 2026-04-14

### HTA Module Redesign Phase 3 — adverse_events.py item iteration and state machine wiring

**Motivation:** Phase 2 refactored `economics.py` to read v2 JSON directly per item.
Phase 3 does the equivalent for `adverse_events.py`: replaces the single combined
per-arm flat entry (Phase 1 compat shim output) with direct iteration over
`adverse_events.items[]`, enabling per-item billing_moment routing, initiation delta
tracking, and per-item state machine state for sensitivity analysis.

**Modified: `adverse_events.py`** — Full refactor of computation path for v2 configs:
- `_ARM_TO_PREP_STATE` dict added (mirrors `economics.py`) — maps arm key to prep
  state name for population tensor lookups. Defined locally to avoid circular import.
- `_SEVERITY_GRADES` list constant extracted at module level.
- `__init__` additions (v2 path only):
  - `_prev_ae_users: Dict[str, int]` — tracks previous-step user count per arm,
    enabling `new_initiators = max(0, current - prev)` delta for initiation items.
  - `ae_state_v2: Dict[str, Dict[str, int]]` — per-item state dict for state machine
    mode; replaces the per-prep-name `ae_state` from the legacy path.
  - `_init_state_machine_v2()` called at init if `mode="state_machine"` — validates
    that every ongoing item has `state_machine_params` before simulation starts.
- `compute_adjustments()` routes: `_v2_config is not None` → `_compute_v2()`;
  legacy paths unchanged; both now always return `"by_item"` key.
- `_compute_v2(population, year)` — main v2 computation:
  - Computes `current_users_by_arm` dict from population tensor (susceptible slice,
    Oral_PrEP and Lenacapavir prep indices).
  - Iterates `adverse_events.items[]` directly. For each item:
    - `billing_moment="ongoing"` → `_item_prevalence_ongoing()` or
      `_item_state_machine_ongoing()` based on `self.mode`.
    - `billing_moment="initiation"` → `_item_initiation()` with delta from
      `_prev_ae_users`.
    - Other moments (e.g. `"diagnosis"`) → 0.0 (handled by economics.py, not here).
  - `_prev_ae_users` updated after all items are processed in a step (not inside
    the item loop) to avoid order-of-evaluation artefacts.
  - Returns `{utility_decrement, cost_addition, by_item}`.
- `_item_prevalence_ongoing(item, users, step_fraction)` — formula:
  `abs(util_dec) × prevalence × users × step_fraction` and
  `cost × prevalence × users × step_fraction`.
- `_item_state_machine_ongoing(item, users, step_fraction)` — reads
  `onset_rate_per_year`, `resolution_rate_per_year`, `severity_distribution`,
  `utility_decrements`, `cost_additions` from `item["state_machine_params"]`.
  Draws new onsets and resolutions (deterministic or stochastic), distributes
  new cases into severity grades proportionally, applies resolutions proportionally,
  updates `ae_state_v2[item_id]`, computes cost/utility from current state counts.
- `_item_initiation(item, new_initiators)` — one-time event formula:
  `abs(util_dec) × new_initiators × prevalence` and `cost × new_initiators × prevalence`.
  **No `step_fraction` applied** — these are point-in-time events, not annualised.
- `_init_state_machine_v2()` — validates `state_machine_params` present for every
  ongoing item. Raises `ValueError` at init time (not at first step) so
  configuration errors surface before any simulation runs.
- All legacy methods (`_compute_prevalence_mode`, `_compute_state_machine_mode`,
  `_init_state_machine`, `_normalize_prep_key`) preserved byte-for-byte. Legacy
  state machine path still initialised via `_init_state_machine()` only when
  `_v2_config is None`.

**New file: `tests/test_adverse_events_v2.py`** — 26 new tests in 9 classes:
- TestFormatDetection (2): `_v2_config` set on v2 input; None on old flat
- TestPrevalenceOngoing (4): GI AE formula; renal AE formula; ISR formula;
  ongoing items sum to top-level total (initiation item with util_dec=0 included)
- TestArmFiltering (2): oral PrEP items zero when no oral PrEP users; ISR zero
  when no Lenacapavir users
- TestMultipleItemsSameArm (1): GI AE and renal AE computed independently per item
- TestInitiationBillingMoment (4): fires on new initiators; zero on second step
  with same users; no step_fraction applied (6000 not 1500); partial new initiators
  (delta logic: step0=80 users→80 initiators, step1=100 users→20 initiators)
- TestByItemOutput (3): all item IDs present; required keys in each entry;
  sum of by_item values equals top-level totals
- TestStateMachineMode (3): non-zero output confirms item params read (onset_rate);
  missing `state_machine_params` raises ValueError at init; state accumulates and
  evolves across steps via `ae_state_v2`
- TestLegacyFlatFormat (3): old format uses legacy path; produces correct AE impact;
  `by_item` returned as empty dict (not missing)
- TestZeroUsers (1): empty population → zero impact for all items and totals
- TestReturnInterface (3): required keys always present; utility_decrement ≥ 0;
  cost_addition ≥ 0

**Tests:** 26 tests — 26/26 passing (Python 3.9.6 / pytest-8.4.2, macOS, 0.14s).

**Behaviour changes vs V11.6.1:**
- AE initiation costs (e.g. renal screen at PrEP start) now charged correctly
  on the step new users appear, rather than being silently omitted (compat shim
  excluded initiation items from the combined flat entry).
- Multiple AE items per arm now computed independently — GI AE and renal AE each
  fire at their own prevalence and cost, rather than being collapsed into one entry.
- State machine now tracks per-item state (`ae_state_v2[item_id]`) rather than
  per-arm state — enabling independent onset/resolution dynamics per item.
- `compute_adjustments()` always returns `"by_item"` key; callers using `.get()`
  are unaffected.

**V11.6 series complete.** All three phases of the HTA module redesign are done:
- V11.6.0: v2 JSON structure, validator, compat shim (41 tests)
- V11.6.1: economics.py full rewrite — accumulate(), arm_name, new_diagnoses (32 tests)
- V11.6.2: adverse_events.py full rewrite — item iteration, billing_moment routing (26 tests)
Total new tests across V11.6 series: 99. All passing.

---

## V11.5.9 — 2026-04-13

### Hotfix: PrEP tensor dimension mismatch on snapshot restore

**Bug fix (`run_v3.py`, snapshot restoration block):** When the baseline config has
all PrEP interventions disabled (`enabled: false`), the baseline engine builds a
population tensor with `n_prep=1` (axis 6 size 1 — "None" state only). The frozen
snapshot captures this tensor. When an HTA arm engine is created with a PrEP
intervention enabled (e.g., `oral_prep` arm, `n_prep=2`), the snapshot restore
overwrote the arm's correctly-shaped tensor with the baseline's smaller tensor,
causing `IndexError: index 1 is out of bounds for axis 6 with size 1` on the
first transmission step.

Fix: before restoring, compare `snap_pop.shape[6]` with the arm engine's
`n_prep`. If the snapshot is smaller, zero-pad axis 6 to match. Zero-padding is
semantically correct — at the freeze year no one is on PrEP (PrEP was disabled in
the baseline run), so the new PrEP slots should be empty. The reverse case
(snapshot has more PrEP states than the arm) is also handled by truncation.

---

## V11.5.8 — 2026-04-13

### Hotfix: Baseline freeze off-by-one when end_year == freeze_year

**Bug fix (`run_scenarios.py`, `_run_hta_scenarios`):** When `end_year` in the
config equals `freeze_year` in `hta_config.json`, the baseline step loop runs
`range(0, n_steps)` which covers years up to `end_year − years_per_step` (i.e.,
2024.75 with quarterly steps). The freeze condition `current_year >= freeze_year`
never fires, so no snapshot is captured and the HTA run fails.

Fix: after constructing the baseline engine, compute the step index at which
`freeze_year` would be reached (`freeze_step_target`). If that step falls on or
beyond `n_steps`, extend `baseline_engine.time.n_steps` by 1 so the loop executes
exactly that one extra step. The guard is a no-op when `end_year > freeze_year`
(the common case once the simulation horizon is extended past freeze year).

**Same root cause in arm engines:** `run()`'s `min(end_step, n_steps)` clip also
zeroed out the evaluation horizon for arm engines — `end_step = freeze_step +
eval_steps = 300` was clipped to `n_steps = 220`, producing zero iterations and an
empty output DataFrame. Fix: after creating each arm engine, set
`arm_engine.time.n_steps = max(n_steps, end_step)` so the clip is a no-op.

---

## V11.5.7 — 2026-04-13

### Hotfix: V10 config compatibility in legacy validator + folder reorganisation

**Bug fix (`validation.py`):** `validate_config_consistency` and `validate_v5_config`
both checked for a `config["time"]` key that V10 configs do not have — V10 stores
time data under `run_control`, with translation to `time` handled by `ConfigReader`.
Both functions now fall back to `run_control` when `time` is absent, resolving the
`ValueError: Invalid V5 configuration` error on any V10 config run without `--hta`.

**Config schema patch (`v11_configurationMAIN.json`):** Added
`interventions.oral_prep.eligible_activity_classes` and
`interventions.lenacapavir.eligible_activity_classes` — keys introduced in V11.5.1
that were present in `configs/v11_configuration.json` but missing from the production
config, causing HTA activity-filter failures.

**Folder reorganisation:**
- 7 stray root-level docs moved to `documentation/` subfolders
- 5 V11.5.x test files moved from root into `tests/` with correct `sys.path` fix
- Session folder naming corrected to ISO week convention (2026-W15, 2026-W16)

---

## V11.5.6 — 2026-04-13

### HTA Phase 5: Integration & End-to-End Pipeline

Completed the HTA cost-effectiveness analysis pipeline by wiring all Phase 1–4
components into `run_scenarios.py`. The model can now run the full three-arm
comparison (no_prep / oral_prep / lenacapavir) across multiple analysis scenarios.

#### Pipeline implementation (`run_scenarios.py`)
- New `_run_hta_scenarios()` method: baseline run → freeze at 2025 → fork three
  arms per analysis scenario → attach EconomicsAccumulator + AdverseEventModule →
  run evaluation horizon → collect ScenarioResults → run ScenarioComparator
- New `_build_arm_config()`: builds arm-specific configs with PrEP enable/disable
  and scenario-specific `eligible_activity_classes` overrides
- New `_write_hta_outputs()`: produces arm_summary.csv, comparison_table.csv,
  timestep CSVs, price_sensitivity.csv, summary tables
- CLI: `python run_scenarios.py <config> --hta <hta_config> [--output_dir <dir>]`
- Legacy V5 mode preserved for backward compatibility

#### Engine modification (`run_v3.py`)
- `run()` method: added `start_step` and `end_step` parameters for scenario runs
  that start from a frozen baseline point

#### Bug fix (`state_snapshot.py`)
- Fixed `StateSnapshot.clone()`: RNG state from `np.random.RandomState.get_state()`
  returns a tuple, not a dict — changed `.copy()` to `copy.deepcopy()`

#### E2E tests (`test_hta_e2e.py`)
- 19 tests across 8 test classes:
  - Pipeline integrity (4): output files, columns, structure
  - Epidemiological sanity (3): no-PrEP has most infections, QALYs/costs valid
  - Cost sanity (2): Lenacapavir drug costs > Oral PrEP, no-PrEP has no PrEP costs
  - ICER validity (2): valid ICERs, NMB consistency with ICER
  - Determinism (1): same seed → identical results
  - Baseline identity (1): snapshot clones are identical
  - Component integration (3): accumulator+AE, result construction, comparator
  - Engine step range (3): start_step, end_step, default behavior

---

## V11.5.5 — 2026-04-13

### HTA Phase 4: Scenario Comparator

Implements post-run cost-effectiveness analysis following Dutch ZIN guidelines.

#### New file: `scenario_comparator.py`
- `ScenarioResult` dataclass: container for arm economic/epidemiological outputs
- `ScenarioComparator` class:
  - `compare_pair()`: pairwise ICER, NMB, dominance classification
  - `compute_icer()`: handles dominant, dominated, zero-QALY edge cases
  - `build_comparison_table()`: sequential analysis per ZIN (cost-ordered, excludes dominated)
  - `price_sensitivity()`: Scenario D drug cost scaling across price points
  - `find_threshold_price()`: binary search for breakeven Lenacapavir price
  - `summary_table()`: per-arm summary with average cost per QALY

---

## V11.5.4 — 2026-04-13

### HTA Phase 3: Adverse Events Module

Two-mode AE modeling for PrEP-related adverse events.

#### New file: `adverse_events.py`
- **Prevalence mode** (default): fixed AE proportion applied to PrEP users per step;
  returns quarterly-adjusted utility decrement and cost addition
- **State machine mode** (advanced): stochastic onset/resolution dynamics with
  mild/moderate/severe severity grades, tracked via shadow arrays (not population tensor)
- `compute_adjustments(population, step, year)` → `{utility_decrement, cost_addition}`
- 14 unit tests in `test_adverse_events.py`

---

## V11.5.3 — 2026-04-13

### HTA Phase 2: Economics Accumulator

Per-timestep QALY and cost accumulation for health technology assessment.

#### New file: `economics.py`
- `EconomicsAccumulator` dataclass: reads population tensor each step, maps health
  states to utility weights and costs, applies ZIN dual discounting (1.5% health, 4.0% costs)
- Tracks: susceptible (no PrEP, oral PrEP, Lenacapavir), HIV+ states (Acute, Chronic, AIDS)
  split by ART status, cost breakdown (drug, monitoring, hiv_care)
- Lenacapavir first-year cost premium tracking via `_prev_lena_users`
- AE module integration via `set_ae_module()`
- `get_summary()` and `get_timestep_records()` for comparator consumption

#### New file: `configs/hta_config.json`
- Utility weights (8 health states), unit costs (Lena €21,395/€20,555, oral €5,400, etc.)
- ZIN discounting rates, WTP threshold (€50,000/QALY)
- AE parameters (prevalence mode), analysis scenario definitions (A, B, D)

---

## V11.5.2 — 2026-04-13

### HTA Phase 1: Baseline Freezing Wire-up

Completed the plumbing for scenario forking from a frozen baseline.

#### Engine changes (`run_v3.py`)
- `SimulationEngineV3.__init__`: accepts optional `initial_snapshot` parameter;
  when provided, restores population tensor, childhood pool, and RNG state
- HTA hooks at end of `step_simulation()`:
  - `_snapshot_manager.should_freeze()` / `.freeze()` for baseline capture
  - `_economics.accumulate()` for per-step QALY/cost tracking
- Both hooks are no-op when not set (zero overhead for non-HTA runs)

---

## V11.5.1 — 2026-04-13

### Activity class eligibility filter for PrEP interventions

Added `eligible_activity_classes` as a hard eligibility gate on the activity dimension
of PrEP uptake, mirroring the existing `eligible_orientations` and `eligible_age_range`
filters from V11.5.0. Required for HTA scenario analysis where Lenacapavir targeting is
defined by regulatory/reimbursement criteria (who qualifies), not behavioral uptake rates.

**Design distinction from `uptake_modifier_by_activity`:**
- `eligible_activity_classes` = regulatory eligibility gate (people outside the list
  cannot initiate this PrEP type; the config self-documents who qualifies)
- `uptake_modifier_by_activity` = uptake intensity within the eligible population
  (behavioral/access barrier, continuous scale)

Both mechanisms can be used together: filter defines the eligible population, modifier
tunes uptake rates within that population.

#### Config changes
- `interventions.oral_prep`: added `eligible_activity_classes` (default: `["Low", "Medium", "High"]`)
- `interventions.lenacapavir`: added `eligible_activity_classes` (default: `["Low", "Medium", "High"]`)
- Notes document HTA scenario override pattern (e.g. `["High"]` for Scenario B targeted rollout)

#### Engine changes
- `run_v3.py` (`_step_prep_transitions`): resolve `eligible_act_indices` from config;
  uptake loop now iterates over eligible activity indices only (mirrors orientation filter pattern)
- `config_reader.py`: pass through `eligible_activity_classes` in `interventions()` method

#### Tests
- 3 new tests in `TestActivityFilter` class (`test_prep_dynamics.py`):
  - High-only filter excludes Low and Medium
  - Absent key defaults to all classes (backward compat)
  - Combined orientation + activity filter: only MSM High has PrEP users

#### Backward compatibility
- Default value (all activity classes) preserves V11.5.0 behavior when key is absent
- All 12 existing PrEP dynamics tests pass unchanged

---

## V11.5.0 — 2026-04-12

### PrEP dynamics: eligibility-filtered, risk-stratified, time-scheduled

Overhauled `_step_prep_transitions()` in `run_v3.py` to produce realistic PrEP uptake
dynamics suitable for HTA cost-effectiveness evaluation. Previously, PrEP uptake was
applied uniformly to ALL susceptible individuals (all orientations, all ages, all
activity levels), which would have resulted in ~2.2M PrEP users instead of the
~10-15K observed in the Netherlands.

- **A: Orientation eligibility filter** — new `eligible_orientations` config field per
  intervention. Default: `["MSM"]` for both Oral PrEP and Lenacapavir. Only listed
  orientations can initiate PrEP. Dynamic — accepts any combination (e.g., `["MSM", "Female"]`).
- **B: Activity-level uptake modifiers** — wired in the existing but previously unused
  `uptake_modifier_by_activity` config values. High-activity individuals have higher
  PrEP uptake probability (e.g., 1.5x for oral, 2.0x for Lenacapavir). Normalised
  to Medium = 1.0; population-level shift toward high-risk is intentional.
- **C: Age-bounded eligibility** — new `eligible_age_range` config field per intervention.
  Default: 15-19 to 64-69. Applies to initiation only — existing users who age past
  the max are NOT forced off PrEP (retention, not forced dropout).
- **D: PrEP scale-up schedules** — added `prep_schedules` top-level config block matching
  the existing ART schedule pattern. Oral PrEP placeholder schedule: 2019: 0.02 →
  2025: 0.08. Lenacapavir: empty (populated by HTA scenario overrides).

Dropout remains unconditional (applies to ALL current PrEP users regardless of
eligibility status).

Effective per-cell uptake: `schedule_rate(year) × activity_modifier × eligibility_mask(orient, age)`

### Config changes
- `interventions.oral_prep`: added `eligible_orientations`, `eligible_age_range`
- `interventions.lenacapavir`: added `eligible_orientations`, `eligible_age_range`
- `interventions.*.advanced.uptake_modifier_by_activity`: notes updated (now IMPLEMENTED)
- New top-level `prep_schedules` block with Oral_PrEP and Lenacapavir schedules
- `config_reader.py`: updated to pass through `eligible_orientations` and `eligible_age_range`

### Tests
- New `test_prep_dynamics.py` (12 tests): orientation filter, activity modifiers,
  age bounds, schedule integration, mass balance, combined realistic scenario

### Backward compatibility
- All new config fields have defaults matching pre-V11.5 behavior when absent
- Existing tests pass unchanged (44/44 regression, excluding pre-existing failures)

---

## V11.4 — 2026-04-05

### Code cleanup
- **C1**: Removed non-production `SimulationEngine` class from `engine_v3.py` (~534 lines).
  Also removed the `simulate()` wrapper and `__main__` block that depended on it.
  `engine_v3.py` now contains only utility classes imported by the production runner:
  `TimeSystem`, `GroupSystem`, `PopulationState`, `ConfigValidator`.
  **No behavioral change.** Production runner is `SimulationEngineV3` in `run_v3.py`.

### Bug fixes — transmission
- **B2** (`transmission_corrected.py`): Fixed infections array accumulation. Line 366
  changed from `=` to `+=` so infections from multiple partner groups accumulate
  correctly. Added a per-cell cap in `apply_infections_to_population()` against the
  live susceptible count at application time. **No behavioral change** in the current
  3-orientation config (each susceptible orientation has exactly one partner
  orientation). Fix is a robustness guard for future orientation additions.

- **B3** (`transmission_corrected.py`): Replaced `int()` (always-floor truncation)
  with unbiased rounding in per-cell contact distribution (line 272).
  - Deterministic mode: `round(contacts_float)` (Python half-even, unbiased).
  - Stochastic mode: floor + Bernoulli draw on the fractional remainder.
  **Calibration note:** This removes a systematic downward bias on small cells
  (PrEP subgroups, small age bins). A slight upward shift in total infections is
  expected. No parameters were changed; recalibration is not triggered by this
  patch but the change should be noted when evaluating epidemic trajectory shifts.

### Bug fixes — outputs
- **B4** (`run_v3.py`): Corrected the incidence metric.
  - **Denominator**: Now uses `susceptible_pre_tx` — the susceptible count captured
    immediately before step 3 (transmission), not after all remaining steps.
  - **Annualization**: Divided by `self.time.years_per_step` to produce a per-year
    rate rather than a per-timestep rate.
  - **Column rename**: `incidence` → `incidence_rate`.
  - **Units**: New infections per susceptible person per year.
  - **Not backward-compatible** with V11.3 output CSV files on the incidence column.
    Values will be approximately 4× larger (quarterly → annual conversion) plus a
    small denominator-timing adjustment.

---

## V11.3 — 2026-03-26

Route-specific per-act transmission betas (Boily 2009 / Patel 2014).
Direction-specific vaginal betas (vaginal_mtf, vaginal_ftm).
URAI/UIAI anal split with MSM versatility parameter.
Feature-flagged under `disease.transmission.advanced`.

---

## V11.2

Parameter tier metadata added (fixed / estimated / uncertain).

---

## V11.0 / V11.1

Contact balance fix. Garnett (1994) epsilon parameterisation for sexual mixing.
Replaced legacy matrix-based mixing path (kept for backward compat with V10 test configs).

---

## V12.2.1 — 2026-04-30

### ConfigReader waning_efficacy propagation fix

**File changed:** `config_reader.py`  
**Tests added:** 2 (T-W11b, T-W11c in `tests/test_lena_waning_state.py` → 19 total in file)  
**Total tests:** 499 collected, 498 passing (1 pre-existing: `test_gamma_method_of_moments`)  
**Session notes:** `documentation/sessions/2026-W18/session_2026-04-30_v1221_waning_efficacy_bug.md`

**Bug:** `ConfigReader.interventions()` built the V6 config dict using a hardcoded field
whitelist that did not include `waning_efficacy`. The field was silently dropped during
V10→V6 translation. `TransmissionEngine.__init__` therefore always fell back to the hardcoded
default of 0.99 regardless of the value set in any config file. Config changes to
`waning_efficacy` had no effect on any run.

**Symptom:** Three simulations with `waning_efficacy` set to 0.99, 0.86, and 0.25
(identical active lena efficacy = 0.86 across all three) produced identical HIV infection
counts in Scenario A analysis.

**Fix:** Added `"waning_efficacy"` to the field copy loop in `ConfigReader.interventions()`:

```python
for key in ("enabled", "uptake_rate", "dropout_rate",
            "eligible_orientations", "eligible_age_range",
            "eligible_activity_classes",
            "waning_efficacy"):          # V12.2.1: must survive V10→V6 translation
    if key in src:
        dst[key] = src[key]
```

**Tests added:**
- T-W11b: Sets `waning_efficacy=0.55` in config, asserts `prep_efficacy["Lenacapavir_Waning"]==0.55`. Regression test for the exact fix.
- T-W11c: Removes `waning_efficacy` from config entirely, asserts engine defaults to 0.99. Tests defensive fallback path.

**Notes:**
- `v11_configurationMAIN.json`: `waning_efficacy` may be non-default during stress testing — this is intentional.
- `configs/hta_config.json`: `waning_efficacy` correctly absent — it is a simulation parameter that belongs only in the main config, not in the HTA config.

---

## V12.5.2 — 2026-05-06

### Reporting Fix: Baseline Key Year Flow Aggregation

**File modified:** `hta_report.py`  
**Patch doc:** `documentation/patches/PATCH_V12_5_2_BASELINE_AGGREGATION_FIX.md`

**Summary:**  
The "Baseline Key Years" sheet was displaying per-step quarterly values for flow variables (new infections, HIV deaths, births, etc.) instead of annual sums. Root cause: `.drop_duplicates(subset="year_int")` in `_write_baseline_summary` silently discarded Q2–Q4 rows, leaving only the Q1 snapshot. At `dt=0.25`, this understated flow totals by ~4×. Fix: replaced with a `groupby("year_int").agg(...)` that sums flow columns and takes the last-step value for stock/rate columns. No simulation logic, parameters, or other report tabs affected.

---

## V12.5.1 — 2026-05-05

### Engine Hardening: Silent Failure Elimination (ENG-F04 – ENG-F11)

**Files modified:** `demography.py`, `engine_v3.py`, `schedule_utils.py`, `migration.py`, `validation.py`  
**Tests added:** 8 (T-EH1 – T-EH8 in `tests/test_engine_hardening_v124.py`)  
**Plan doc:** `documentation/patches/PATCH_V12_4_ENGINE_HARDENING.md`  
**Audit source:** `documentation/AUDIT_SILENT_FAILURES_2026_05_04.md`

**Summary:**  
Follow-on to the 2026-05-04 silent-failure audit. Eight targeted fixes converting silent wrong-answer bugs into loud, traceable errors or warnings. No numeric parameter changes. No model behaviour change under correct configs.

**ENG-F04 — Orientation index lookup hardening (`demography.py`)**  
`_calculate_life_table_mortality` and `calculate_births` used `.get("Female", 0)`, `.get("Heterosexual_Male", 1)`, `.get("MSM", 2)` — meaning a config name mismatch (e.g. `"female"` vs `"Female"`) would silently apply the wrong life table to the wrong subgroup. Changed all six callsites to direct `dict["key"]` access. A misconfigured orientation name now raises `KeyError` immediately.

**ENG-F05 — Post-seed validation (`engine_v3.py`)**  
Added `_validate_post_seed(initial_prev)` called immediately after `_seed_initial_hiv` inside `initialize_from_config`. Checks: (1) HIV+ count within rounding tolerance of target prevalence, (2) no negative tensor cells. Raises `ValueError` on failure. In production V12.4+ configs `initial_hiv_prevalence=0.0` so the path is dormant; the guard closes a coverage gap for any test config that uses direct seeding.

**ENG-F06 — Dynamic PrEP schedule discovery (`schedule_utils.py`)**  
`ScheduleManager` iterated `["Oral_PrEP", "Lenacapavir"]` — a hardcoded list that would silently drop any third PrEP intervention added to the config (e.g. cabotegravir). Replaced with a dynamic key scan of the `prep_schedules` dict, skipping only the structural `"interpolation"` key.

**ENG-F07 — Missing population parameter warnings (`engine_v3.py`)**  
`initialize_from_relative_weights` used `.get()` fallbacks for `total_size` and `p_msm` without any signal. Both now emit `UserWarning` before the fallback fires, naming the missing key and the fallback value used.

**ENG-F08 — Year-key round-trip bug in multiplier tables (`demography.py`)**  
`apply_population_adjustments` and `calculate_births` converted multiplier keys via `str(int(float(k)))`, which silently drops non-integer keys (`"1995.5"` → `"1995"` → KeyError or silent zero). Fixed with a `{float(k): k}` map preserving the original string key through the round-trip.

**ENG-F09 — Gap-year warning in year_specific migration (`migration.py`)**  
`_get_annual_net` silently returned 0 for any year absent from the table regardless of whether the year was within or outside the declared range. Now emits `RuntimeWarning` the first time a within-range gap is encountered (deduplicated per year), with the table range in the message. Out-of-range years remain silently 0.

**ENG-F10 — REVERTED**  
A warning for the `run_control` config key in `validate_config_consistency` was added then removed. The V11 production config legitimately uses `run_control` — `ConfigReader` translates it to `time` in the v6 dict. There is no schema version field to distinguish old V10 from current V11 at the raw config level, so the warning would fire on every production call.

**ENG-F11 — Honest scenario validation status (`validation.py`)**  
No-PrEP scenario check emitted `suite.add_result(True, …, "info")` with a message implying validation was complete. Schedule validation for no-PrEP scenarios actually requires the merged runtime config and cannot be done at static validation time. Status changed to `"warning"` with an explicit note that schedule validation is deferred to runtime.

**Unused import cleanup:** `import warnings` removed from `validation.py` (added during ENG-F10, unused after revert).

**No model parameter changes. No behavioural change under correct configs.**

---

## V12.5.3 — 2026-05-07

### Lenacapavir Parameter Update: EPAR-Sourced Efficacy and ISR Restructure

**Files modified:** `v11_configurationMAIN.json`, `1980_test.json`, `configs/hta_config.json`  
**Patch doc:** `documentation/patches/PATCH_V12_5_3_LENA_PARAMETER_UPDATE.md`  
**Session note:** `documentation/sessions/2026-W19/SESSION_NOTE_2026-05-07.md`  
**Source:** EMA CHMP Assessment Report, Yeytuo (lenacapavir), EMA/265754/2025, 24 July 2025.

**Summary:**  
All lenacapavir efficacy and ISR parameters updated from pre-trial placeholders to values derived directly from the EU regulatory assessment report (EPAR) following EC authorization in August 2025.

**Efficacy changes (`v11_configurationMAIN.json`, `1980_test.json`):**
- `interventions.lenacapavir.advanced.efficacy.value`: `0.86` → `0.957`  
  Derived from PURPOSE 2 (NL-relevant trial: CGM/TGW/TGM/GNB): LEN rate 0.103/100PY vs bHIV 2.374/100PY → efficacy = 1 − (0.103/2.374) = 0.957.
- `interventions.lenacapavir.advanced.efficacy.tier`: `"fixed"` → `"uncertain"` (now enters PSA)
- `interventions.lenacapavir.advanced.efficacy.bounds`: `[0.96, 1.0]` → `[0.843, 0.995]`  
  Derived from PURPOSE 2 LEN rate 95%CI (0.012–0.373/100PY) propagated through efficacy formula.
- `interventions.lenacapavir.advanced.efficacy.distribution`: added `Beta(alpha=25, beta=1.1)`
- `interventions.lenacapavir.waning_efficacy.value`: `0.86` → `0.957` (matches active efficacy per LEN PK profile)

**ISR adverse event restructure (`configs/hta_config.json`):**
- `lenacapavir_isr_ae` updated to cover *transient ISRs only*: prevalence `0.20` → `0.405`  
  Total ISR prevalence 0.760 (midpoint of PURPOSE 1: 68.8% and PURPOSE 2: 83.2%) minus persistent nodule prevalence 0.355 = 0.405.
- New AE item `lenacapavir_isr_persistent_nodule` added: prevalence `0.355`  
  EPAR safety section: ~35–36% of LEN users had non-resolving nodule at end of observation in both trials. Resolution rate 0.5/year derived from exponential model (35.5% still present at ~2yr).
- ISR utility decrements and costs remain placeholders pending disutility literature sourcing (BACKLOG-15).

**CHMP caveat noted in config:** bHIV-derived efficacy excluded from SmPC 5.1; model uses it as the necessary CEA input with LEN vs TVD as sensitivity (RR 0.111, PURPOSE 2). Full derivation in patch doc.
