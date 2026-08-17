# Phase 1 — Model Bug & Correctness Audit

**Model:** HIV transmission + cost-effectiveness model, `Modeller/v11`
**Reviewer role:** critical academic collaborator (peer-review conditions)
**Scope reviewed:** core transmission engine, health economics, calibration (ABC-SMC / gates / distance), PSA/HTA machinery
**Date:** 2026-07-06
**Reference config audited:** `V13.22_20260604_nearfit_rank001_basecase.json`, `configs/psa_config.json`, `configs/hta_config.json`

---

## How to read this document

Each finding is tagged with a **severity** and an **evidence status**, in the spirit of the project's writing standards:

- **Severity:** `CRITICAL` (threatens the headline CE conclusion or its defensibility) / `HIGH` / `MEDIUM` / `LOW`.
- **Evidence status:** `Confirmed code fact` (I read the exact lines) vs. `Judgment call` (a modelling/defensibility opinion, not a code defect).

Important framing: **this is a mature, heavily-iterated codebase** (≈34k lines, 70 test files, extensive changelog and per-parameter sourcing). Most of what follows are *methodological/validity* issues a thesis examiner or journal reviewer would raise, **not** crashing bugs. I found no evidence of a gross arithmetic error in the ICER, discounting, or mass-balance logic — those are implemented cleanly. The risks are concentrated in **how uncertainty is propagated** and **a small number of biology/ordering approximations.**

---

## Summary table (triage)

| # | Finding | Severity | Evidence |
|---|---------|----------|----------|
| 1 | Calibration (estimated-tier) uncertainty is **not** propagated into the PSA/CEA | CRITICAL | Confirmed |
| 2 | "ABC-SMC" resampling is not a valid posterior sampler (1/distance weights, fixed kernel) | HIGH | Confirmed |
| 3 | Same-step diagnosis→ART "rescue" can suppress AIDS deaths (event-ordering bias) | HIGH | Confirmed |
| 4 | "Societal" base case excludes productivity losses + informal care | HIGH | Confirmed |
| 5 | 40-year horizon labelled/treated as lifetime; late-horizon truncation | MEDIUM | Confirmed |
| 6 | Force of infection summed additively across stages/ART (not competing-risk) | MEDIUM | Confirmed |
| 7 | Heterosexual mixing geometric-mean pool breaks marginal contact-supply balance | MEDIUM | Confirmed |
| 8 | Background mortality added on top of HIV mortality; deterministic path uses cellwise rounding | MEDIUM | Confirmed |
| 9 | PSA parameters sampled independently (no correlation structure) | MEDIUM | Confirmed (acknowledged in code) |
| 10 | Extended (weak) dominance not tested in the CE frontier | LOW | Confirmed |
| 11 | Wide calibration acceptance bands (e.g. incidence ratio 0.50–1.75) | LOW–MEDIUM | Confirmed |
| 12 | Minor: stale default wage, per-step ART-mortality multiplier, stale README | LOW | Confirmed |

---

## CRITICAL

### 1. Calibration uncertainty is not propagated into the PSA / cost-effectiveness result
**Evidence: Confirmed code fact.**

- `configs/psa_config.json` → `sampling.tiers_to_sample.value = ["uncertain"]`.
- `psa_sampler.py` (`PSASampler.__init__`) builds the draw matrix only from `get_params_by_tier(raw_config, tier)` for the tiers listed → **only the 6 Tier-3 "uncertain" parameters vary.**
- In the audited base case, the **10 "estimated" (calibrated) parameters are held fixed at the single rank001 point**, including *all four route-specific transmission betas* (`vaginal_mtf/ftm`, `anal_receptive/insertive`) and *both* mixing parameters (`epsilon_hetero`, `epsilon_msm`), plus `p_msm`, seed prevalence, and seed MSM fraction (verified by walking the config `tier` fields).
- `run_psa_hta.py:277` sets `deterministic_mode = True` for every PSA draw.

**Why this matters (defensibility).** The parameters that most directly determine *infections averted* — the transmission betas and the assortativity/mixing structure — are exactly the ones **frozen** during PSA. The reported CEAC / credible interval on the ICER therefore reflects uncertainty in only 6 secondary parameters (stage multipliers, MSM versatility, `chronic_to_aids`, ART mortality reduction, lenacapavir efficacy, and the HTA cost block) while treating the calibrated epidemic engine as if it were known with certainty. Under ISPOR-SMDM good-practice guidance and ZIN's requirement to characterise decision uncertainty, **calibrated-parameter uncertainty is a first-order source that is currently omitted.** A reviewer will read the PSA as *understating* decision uncertainty.

**This is the single most important issue for the model's defensibility.** It is not a code defect — it is a design decision (`tiers_to_sample` even documents that `"estimated"` can be added) — but it needs to either be fixed or explicitly defended.

**What would resolve it (in order of rigour):**
1. Sample the calibrated parameters jointly from the *accepted* calibration ensemble (the ABC particles / near-fit set) and run PSA over that joint set — the correct way to carry calibration uncertainty. (Depends on Finding 2 being addressed for the weighting to be meaningful.)
2. At minimum, add `"estimated"` to `tiers_to_sample` with defensible marginal distributions + a correlation structure, and show the ICER distribution widens.
3. If neither is feasible before the deadline, state explicitly in the manuscript that the PSA conditions on the point-calibrated epidemic parameters and is therefore a *lower bound* on decision uncertainty, and support the point estimate with scenario analyses over alternative calibrated fits (e.g. rank001 vs rank012, which already exists in `configs/`).

---

## HIGH

### 2. The "ABC-SMC" calibrator is not a valid posterior sampler
**Evidence: Confirmed code fact.**

`run_abc_smc_calibration.py`:
- `_resample_particles` (line ~428): `weights = 1.0 / (dist_arr + 1e-9); weights /= weights.sum()`. Particles are resampled with probability **proportional to inverse distance**.
- `_perturb_particle` (line ~378) + `_bandwidth` (line ~116): Gaussian perturbation kernel with a **fixed bandwidth = 10 % of each parameter's prior range**, independent of the current population's spread.
- There is **no importance-weight correction** of the form `w_i ∝ π(θ_i) / Σ_j w_j K(θ_i | θ_j)`.

**Why this matters.** A proper ABC-SMC (Sisson et al. 2007; Toni et al. 2009; Beaumont et al. 2009) accepts on an indicator `{d(θ) < ε_t}` and reweights each surviving particle by the prior over the kernel-mixture density, with the perturbation kernel typically set to **twice the weighted empirical variance** of the previous population. This implementation instead does a heuristic distance-weighted evolutionary resample with a static kernel. Consequently:
- The particle ensemble **does not approximate the Bayesian posterior**; its spread is governed by the arbitrary 10 % kernel, not by the data.
- Any statement in the manuscript of the form "posterior distribution", "credible interval", or "ABC-SMC posterior" over the calibrated parameters is **`Unsupported`** as written.

**Mitigating fact:** the pipeline appears to select the **single minimum-distance particle** (`rank001`) as the base case. A *point* selected by minimum distance is unaffected by the (incorrect) resampling weights, so the base-case point estimate is not corrupted by this. The problem is strictly about **claiming posterior/uncertainty semantics** from the ABC output.

**Resolution options:**
1. If you need genuine posterior uncertainty (and you do, per Finding 1): implement the Toni/Beaumont weights + adaptive kernel, or switch to the already-present **SNPE** path (`run_snpe_calibration.py`, `sbi==0.26.1`), which *is* a principled neural posterior estimator, and draw the PSA epidemic parameters from that posterior.
2. If you keep this method: **rename it** in the manuscript (e.g. "sequential distance-weighted calibration" / "iterative importance-like calibration") and do **not** report ABC particles as a posterior. Present the near-fit ensemble as a set of *plausible fits* for scenario analysis, not credible intervals.

### 3. Same-step diagnosis → ART transition can suppress AIDS deaths
**Evidence: Confirmed code fact.**

`run_v3.py` `step_simulation`, production order (`variant == "current"`): step 4 `_step_disease_progression()` → step 5 `_step_diagnosis()` → step 6 `_step_art_transitions()` → **HIV mortality is only applied later in step 10** (`_step_hiv_mortality`).

Within a single quarterly step, an individual can: progress Chronic→AIDS, then be diagnosed, then initiate ART (moving to AIDS/on-ART), and then have mortality drawn with the **ART-reduced** rate (`_step_hiv_mortality`: `reduced_rate = mortality_rate * (1 - art_reduction)`, with `art_transmission_reduction`/`art_mortality_reduction ≈ 0.8–0.96`). The high AIDS mortality (`disease.mortality.aids ≈ 0.244/yr`) is therefore partially rescued *in the same step it is incurred*, biasing modelled AIDS deaths **downward**.

**Confirming signal that this is real:** the code already contains diagnostic event-order variants (`V13.16`: `mortality_before_diagnosis_art`, `art_lag_after_diagnosis`, etc.) built specifically to test "whether same-step diagnosis/ART rescue suppresses AIDS deaths." That the authors built these variants indicates the concern is live and unresolved in the default.

**Why it matters for the CEA.** AIDS deaths and late-stage person-time drive a large share of averted HIV costs and QALY losses. A systematic downward bias in AIDS mortality compresses the difference between arms and could move the ICER. Quarterly steps make same-step rescue non-trivial.

**Resolution:**
1. Run the existing `mortality_before_diagnosis_art` diagnostic variant and report the AIDS-death delta vs. the production order — this quantifies the bias directly (you already have the tooling).
2. If material, adopt an ordering that applies stage-entry mortality before same-step ART benefit, or apply ART mortality benefit with a one-step lag for newly-initiated patients.
3. At minimum, document the ordering choice and the sensitivity of AIDS deaths / the ICER to it.

### 4. The "societal" base case omits productivity losses and informal care
**Evidence: Confirmed code fact.**

`configs/hta_config.json` → `perspective.primary = "societal"`, but:
- `perspective.productivity_loss.enabled = False` (base case),
- `perspective.informal_care.enabled = False` ("Not implemented in this patch"),
- the only societal addition actually active is `patient_time_costs` (PrEP monitoring + HIV on-ART monitoring visits, valued at the average wage).

`economics.py` mirrors this: `_compute_productivity_losses` returns `(0.0, {})` when disabled, so the base-case "societal" total is **payer costs + patient-time only.**

**Why it matters.** ZIN 2022 mandates the societal perspective as primary and explicitly includes productivity and informal care. Labelling this base case "societal" while excluding two of its defining components is exactly the kind of thing a reviewer (and, per your own notes, your defense committee) will challenge. The config's `_base_case_note` argues the exclusion is *conservative for prevention* (it removes productivity **savings** from infections averted) — that argument is reasonable and should be made explicitly in the text, but the perspective should not be presented as a full societal analysis.

**Resolution:**
1. Either (a) re-enable productivity losses (`friction_incident` method is already implemented and iMTA-sourced) for the base case, or (b) rename the base case "healthcare payer + patient time" and present full societal as a scenario.
2. State the direction of bias from excluding productivity/informal care (you already argue it is conservative for the intervention — keep that, but show it in a sensitivity run).
3. Implement or explicitly scope-out informal care (`BACKLOG-20`).

---

## MEDIUM

### 5. 40-year evaluation horizon vs. "lifetime" claim; late-horizon truncation
**Evidence: Confirmed code fact.** `configs/hta_config.json` → `time_horizon.evaluation_years = 40`; `run_scenarios.py:383` `end_year = freeze_year + eval_years` (2025 → 2065). The dynamic model is not a cohort-lifetime model.

- **Consistency check:** if the manuscript says "lifetime horizon", that conflicts with a 40-year dynamic horizon — a model–writing mismatch to fix.
- **Truncation bias:** infections occurring near year 40 have their downstream lifetime HIV costs and QALY losses cut off at the horizon. Because prevention's benefit is the *avoidance* of those downstream streams, truncation **understates** the benefit of the more effective arm — i.e. conservative for lenacapavir, but it should be stated and ideally tested at a longer horizon (e.g. 60–70 years) as a scenario.

### 6. Force of infection is summed additively across stages and ART status
**Evidence: Confirmed code fact.** `transmission_corrected.py` `calculate_transmission`: per susceptible cell, infections are accumulated as `cell_infections += inf_off` / `+= inf_on` across the Acute/Chronic/AIDS × off/on-ART passes, then capped at `susceptible_pop`. This is a **sum of per-source expected infections**, not a competing-risk combination `1 − ∏(1 − p_source)`.

At the low per-act betas here (chronic ≈ 0.0004–0.014) the linear approximation is close, but at high partner-group prevalence the additive form **overestimates** the hazard, and the susceptible-cap then silently absorbs the overshoot. Acceptable as a documented approximation; flag it and, ideally, switch to the exponential/competing-risk form `p = 1 − exp(−Σ λ)` for cleanliness and defensibility.

### 7. Heterosexual geometric-mean pool does not preserve marginal contact supply
**Evidence: Confirmed code fact.** `run_v3.py` `_step_contacts_and_mixing` (epsilon mode): `pool = sqrt(female_side_pool * male_side_pool)` assigned symmetrically to both directions.

Symmetric assignment **does** guarantee pair balance (`contacts[F→M] == contacts[M→F]`), which is the property the docstring claims. But the geometric mean of the two sex-specific demand pools means the **realised total number of contacts for each group generally differs from its configured contact supply** (`total_contacts`). This is the standard, unavoidable tension in Garnett-style mixing (you cannot simultaneously satisfy both sexes' supply constraints under fixed assortativity); the geometric mean is a legitimate reconciliation, but it is an **approximation of the supply constraint, not an identity.** Document it as such, and check ICER sensitivity to `epsilon_hetero` (which is itself calibrated and frozen in PSA — see Finding 1).

### 8. Background (all-cause) mortality is applied on top of HIV mortality; deterministic path rounds cellwise
**Evidence: Confirmed code fact.** `demography.py` `_calculate_life_table_mortality` applies life-table survival to **all** compartments including HIV+ (Acute/Chronic/AIDS), and `run_v3.py` step 10 applies HIV mortality separately. Two sub-issues:
- **Double counting risk:** if `disease.mortality.{chronic,aids}` are intended as *all-cause* HIV-stage mortality rather than *excess* mortality, layering background mortality over-counts deaths for HIV+ (mostly affects AIDS, whose background rate is small, so the effect is minor but should be stated: HIV mortality params should be documented as *excess* rates).
- **Deterministic inconsistency:** background mortality uses cellwise `np.round(pop * rate)` in deterministic mode, whereas transmission and other events use the aggregate-preserving `_deterministic_allocate_events` allocator. Cellwise rounding can erase rare deaths spread over many cells — the exact failure mode the aggregate allocator was written to prevent. Since PSA runs deterministically (Finding 1), this inconsistency is in the analysis path. Route background mortality through the same allocator.

### 9. PSA parameters are sampled independently (no correlation structure)
**Evidence: Confirmed (explicitly acknowledged in code).** `run_psa_hta.py:24, 598` and `hta_sampler.py:23` — "Params sampled independently (no correlation groups) [BACKLOG-1]"; epi and HTA draws use offset seeds and independent LHS. Independence is a common simplifying assumption, but it is a stated limitation that interacts with Finding 1: even the 6 varied parameters ignore any joint structure (e.g. stage multipliers are biologically linked). Note in limitations; consider correlated sampling for the cost block at least.

---

## LOW

### 10. Extended (weak) dominance not evaluated on the CE frontier
`scenario_comparator.py` `build_comparison_table` orders arms by cost and excludes **strongly** dominated strategies (higher cost, lower QALY), but does not test **extended dominance** (a strategy whose ICER exceeds that of a more effective option). For the current 3–4 arm set with a clear efficiency ordering this is usually immaterial, but a rigorous CEA states that extended dominance was checked. Add the check or state it was verified manually.

### 11. Calibration acceptance bands are wide
`calib_acceptance_gates.py` `AcceptanceGateSettings`: e.g. incidence recent ratio floor/ceiling = **0.50 / 1.75** (a 3.5× admissible range), diagnosis ratio 0.90–1.50, PLHIV/ART recent MAPE ≤ 10 %. A candidate can be "accepted" while materially off on incidence. Wide gates weaken the claim that the model is tightly constrained by Dutch data and, combined with Finding 1, mean the frozen calibrated point could sit anywhere in a fairly large accepted region. Recommend: report the gate widths transparently, and show the CE result is stable across several accepted fits (scenario analysis), not just rank001.

### 12. Minor items
- **Stale default wage:** `economics.py` `_compute_patient_time_costs` default `average_hourly_wage = 36.50` vs. friction default `39.88`. Harmless (config supplies 39.88) but remove the stale literal to avoid a silent wrong value if the config key is ever missing.
- **Per-step ART mortality multiplier:** `reduced_rate = mortality_rate * (1 - art_reduction)` multiplies an already per-step probability; for small rates fine, but converting a *reduced annual* rate then annual→step would be marginally cleaner.
- **README drift:** `README.md` still describes V11.2 (a 5-D tensor and a ~7-file model); the live model is a 7-D tensor `(age, orient, activity, hiv, diagnosis, art, prep)` with ABC-SMC/SNPE/LHS calibration, PSA and HTA reporting. Update before submission so the code's self-description matches (model–writing consistency).

---

## What I checked and found *correct* (so you can defend it)

- **ICER / NMB logic** (`scenario_comparator.py`): `ICER = ΔC/ΔQ`, dominance classification, and `NMB = WTP·ΔQ − ΔC` are implemented correctly, with a sensible `QALY_TOLERANCE` guard and a societal/payer perspective switch.
- **Discounting** (`economics.py`): separate health (1.5 %) and cost (4.0 %) ZIN rates, applied as `1/(1+r)^(year − base_year)` with `base_year = 2025`; no silent fallback (missing keys raise). Correct and guideline-consistent.
- **Mass balance:** every step validates a global population balance (`validate_global_population_balance`) accounting for births, deaths, migration, adjustments and seeding; transmission has per-pass and post-accumulation susceptible caps.
- **PSA determinism:** running each draw in `deterministic_mode` correctly avoids conflating Monte-Carlo stochastic noise with parameter uncertainty — a good choice.
- **Reproducibility primitives:** fixed seeds (`psa_config.seed = 12345`, `n_draws = 1000`, LHS), git provenance capture in the calibration runner, and per-parameter `source`/`distribution_status` metadata in the configs are strong.
- **Two-step transmission formulation** (draw HIV+ contacts, then draw infection) correctly avoids the classic prevalence double-counting, and the deterministic aggregate allocator preserves rare-event expectations.

---

## Suggested verification actions (cheap, high-value, before the defense)

1. **Quantify Finding 3:** run `event_order_variant = mortality_before_diagnosis_art` and diff AIDS deaths + the ICER vs. production. (Tooling already exists.)
2. **Quantify Finding 1:** add `"estimated"` to `psa_config.tiers_to_sample`, re-run a small PSA (n=100), and observe how much the ICER credible interval widens. Even if you don't adopt it, you'll know the magnitude of what's being omitted.
3. **Quantify Finding 5:** re-run the base case at `evaluation_years = 60` and report ICER drift from truncation.
4. **Stability across fits:** run the CEA on `rank001` and `rank012` (both in `configs/`) and report whether the conclusion holds.
