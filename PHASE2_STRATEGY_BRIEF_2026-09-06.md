# Phase 2 Strategy Brief — Model, Calibration, and Manuscript Direction

**Created:** 2026-09-06
**Status:** working strategy document. Complements, does not replace, `PHASE2_DEVELOPMENT_CHECKLIST.md` (the operational gate list).
**Purpose:** capture the reasoning, evidence, and decisions from the 2026-09-06 strategy session in enough detail to (a) resume work in a new session without re-deriving anything, and (b) prevent already-rejected paths from being relitigated.

**How to use this file:** read §1–§4 for state, §5 for the blocking question, §6–§7 for the methods to adopt, §8 for manuscript decisions, §9 for the sequenced plan, §12 for what NOT to do. Feed decisions from §10 into the checklist's Decision Log.

---

## 1. Where we are

The thesis was submitted 2026-06-08 and defended 2026-06-30. Phase 2 converts it into a publishable manuscript. Post-defense work has been substantial (repo reorganization, ABC-SMC importance weighting, several real bug fixes) but the model has **no valid production baseline** at this moment — see §4.3.

### 1.1 What is genuinely strong

- **Demographic scaffolding.** HMD fertility/mortality, CBS migration, residence queue, orientation seeding, 1975→2025. Most published PrEP CEAs use static cohorts or fixed-incidence decision trees. This is the model's real contribution and nothing in this session called it into question.
- **Engineering discipline.** 1,507 passing tests, an explicit gate/audit framework, acceptance gates that actually fail loudly rather than silently passing.
- **HTA layer.** Full costing construction, price-threshold machinery, PSA infrastructure, scenario comparator.

### 1.2 What is not working

Three distinct problems, in descending order of severity:

1. **Possible numerical-resolution failure in the incremental analysis** (new, discovered 2026-09-06, unconfirmed) — §5. Blocks interpretation of every CEA result.
2. **The epidemiological layer is effectively MSM-only** — §3.1. A model-credibility problem with a now-decided fix.
3. **Calibration tolerances are partly arbitrary and targets are epistemically mixed** — §3.3, §6. A methodology problem with a clear, citable upgrade path.

---

## 2. Reference facts and file paths

| Thing | Value / path |
|---|---|
| Current nominal base config | `configs/V13.22_20260604_nearfit_rank001_basecase.json` |
| Its provenance | ABC-SMC, particle 10919, particle_seed 12243, generation 2, rank 1, base_seed 1324 |
| Its gate status | `overall_accept = false`, 4 failures, 2 warnings |
| Reachability audit | `outputs/calibration/target_reachability_audit_20260906/TARGET_REACHABILITY_AUDIT.md` |
| Decision-relevance probe | `outputs/hta_results/non_msm_decision_relevance_probe_20260906/` |
| Validation data | `../data/raw/validation/DiseaseData.xlsx` |
| Operational checklist | `PHASE2_DEVELOPMENT_CHECKLIST.md` |
| Defense weak-point brief | `../writing/DEFENSE_PREP_BRIEF_2026-06-30.md` |
| Test suite (2026-09-06) | 1,507 passed / 59 failed / 2 skipped / 5 errors |
| Git state | single commit `0ffdbd5`, ~10 modified files uncommitted |

### 2.1 The 4 acceptance-gate failures on the nominal base case

```
incidence_gate:new_infections:recent_median_ratio_2.696>1.750
non_msm:2024:ratio_0.000<floor_0.500
non_msm:recent_mape_100.0%>35.0%
non_msm:hist_mape_100.0%>60.0%
```

Warnings (soft-gated, so not counted in the 4):
```
aids_death_gate:aids_deaths:bounds_fraction_0.000<0.400
aids_death_gate:aids_deaths:peak_to_recent_mean_ratio_0.935<2.000
```

The two warnings mean **the model does not reproduce the 1990s Dutch AIDS mortality peak at all**. This is the second thing a reviewer will look at after Figure 1.

### 2.2 Superseded thesis headline numbers (DO NOT reuse as a baseline)

Kept only for reconciliation reference. See §4.3 for why these are superseded.

- Lowest lenacapavir ICER: €346,408/QALY (10% substitution)
- Threshold price for cost-effectiveness at €80,000/QALY: ~€9,987/yr (~24% of €41,110 list)
- Oral PrEP expansion (Scenario C): €284,198/QALY, 703 infections averted
- Oral PrEP vs no-PrEP attribution context: €122,477/QALY

---

## 3. What we established this session

### 3.1 The model is MSM-only in practice

From the reachability audit (4,000-particle sweep):

| target | model output | real target | verdict |
|---|---|---|---|
| `new_diagnoses` (gen0 mean) | 5,156.44 | 517 (2024) | reachable |
| `new_diagnoses_msm` (gen0 mean) | 5,156.42 | 318 (2024) | reachable |
| `new_diagnoses_non_msm` (gen0 mean) | **0.03** (max 14 across all 4,000) | **199 (2024)** | **unreachable, max abs r = 0.12** |
| `plhiv_female_15plus` | max 3,649 | 4,726.55 | capped at 77% |

`new_diagnoses` and `new_diagnoses_msm` are identical to two decimal places. Non-MSM is ~38% of real Dutch diagnoses and the model produces essentially zero of it.

**This is not a disclosable limitation — it is a scope violation.** A reviewer plotting Figure 1 against Stichting HIV Monitoring sees a flat line where a third of the epidemic belongs.

Secondary finding: `orient_msm` dominates almost every target (r = +0.49 to +0.59 across PLHIV, incidence, both diagnosis series, AIDS deaths, ART stock). The calibration is therefore **under-identified**: many parameters, but effectively one dominant target measured several ways.

### 3.2 CORRECTION to a prior working assumption — the non-MSM data exists

The working assumption had been "we only have non-MSM/migrant data from 2018 onwards." **This is wrong for diagnoses.**

`DiseaseData.xlsx` → sheet **`Stiching 1.1 (Diagnoses)`** contains `Other men (CALC)` + `Women (CALC)` **annually 1996–2024**, plus a `<=1995` lump of 1,280 (719 men + 561 women):

| year | non-MSM dx | year | non-MSM dx |
|---|---|---|---|
| 1996 | 259 | 2013 | 288 |
| 1997 | 326 | 2014 | 284 |
| 2000 | 406 | 2015 | 298 |
| **2003** | **535 (peak)** | 2017 | 239 |
| 2004 | 532 | 2018 | 214 |
| 2006 | 427 | 2020 | 168 |
| 2008 | 398 | 2022 | 198 |
| 2010 | 378 | 2023 | 212 |
| 2012 | 339 | 2024 | 199 |

The 2018-onwards limit applies only to `migrant_hiv_entries`, and **that sheet is weak evidence anyway**: 322 flat for 2018–2021 (derivation literally reads "Backcast from SHM average"), 641 observed 2022, 394 residual 2023, 302 observed 2024.

**Implication:** the quantity we cannot observe (migrant HIV import volume, pre-2018) is not the quantity we need. The quantity we need (non-MSM diagnoses) we already have, for 29 years.

### 3.3 Trend correction — the "rising heterosexual epidemic" is compositional

Absolute non-MSM diagnoses are flat-to-declining: 214 (2018) → 199 (2024).
The non-MSM **share** rose 31.8% → 38.5%, but only because MSM+Trans collapsed 460 → 318.

This is a compositional shift, not an epidemiological one. It weakens the case for modelling heterosexual transmission dynamically and strengthens the case for treating it as a slowly-varying exogenous background.

### 3.4 UNAIDS targets are not independent evidence

Verified: for Western European countries with strong surveillance — the Netherlands explicitly named among TESSy reporters — UNAIDS estimates are produced by **CSAVR** (Case Surveillance and Vital Registration) inside Spectrum. CSAVR takes new HIV diagnoses from case surveillance and HIV-related deaths from vital registration and fits a **Bayesian back-calculation** to them.

Consequences:

- UNAIDS PLHIV and incidence for NL are a **deconvolution of the same diagnosis and death data already used as targets**. Weighting them as separate targets double-counts the underlying information.
- Model-vs-UNAIDS disagreement on incidence, when both fit the same diagnoses, is a **difference in progression / diagnosis-rate / undiagnosed-fraction assumptions between two back-calculations of one dataset** — not data contradicting the model.
- Their natural role is **cross-model validation** (which also fills the missing ISPOR-SMDM cross-validation slot), not calibration.

### 3.5 Path B (fix non-MSM via transmission parameters) is dead, quantified

The decision-relevance probe swept the two heterosexual route betas (`vaginal_mtf` / `vaginal_ftm`), which are the model's own designated PSA-only uncertainty channel for MSM/non-MSM composition:

| variant | vaginal_mtf | vaginal_ftm | non-MSM dx 2024 |
|---|---|---|---|
| Zero | 0 | 0 | 0 |
| Baseline | 0.00080 | 0.00040 | 0 |
| Literature upper bound | 0.00110 | 0.00140 | 0 |
| Matched to observed | 0.01248 | 0.00624 (~15.6x) | 202 |

Reaching the observed ~199/yr requires betas **11–40x past the literature 95% CI**. This is a definitive, quantified reason never to attempt the mechanistic-transmission fix. It belongs in the manuscript.

---

## 4. Problems the probe surfaced that it did not intend to

### 4.1 The probe did not test what it set out to test

Three of four variants produced non-MSM = **0** — identical on the probed quantity. Yet ΔQALY at `switch_10` moved across them:

| variant | non-MSM dx | ΔQALY @ switch_10 |
|---|---|---|
| Zero | 0 | −25.1 |
| Baseline | 0 | −36.9 |
| Literature upper | 0 | **+140.8** |
| Matched | 202 | −191.9 |

A ~166-QALY swing including a **sign flip**, produced by a lever that demonstrably did nothing to its own target. Whatever moved the answer, it was not the non-MSM channel.

The probe's headline ("non-MSM is not decision-relevant") may well be true — but for the structural cancellation reason in §7.2, not because of this run. **Do not cite the probe's conclusion in the manuscript.** Do cite its Path-B finding (§3.5), which is solid.

### 4.2 Supporting evidence for a numerical-resolution problem

From the baseline run's `comparison_table.csv`:

- `infections_averted` is **non-monotonic** in switch level: 38 → 7 → 34 → 113 → 96 for switch 10/25/50/75/100. No dose-response.
- ΔQALY is **negative in every cell** while `infections_averted` is **positive in every cell**. Internally inconsistent.
- Scale: ΔQALY ≈ −37 against `ref_person_years` = 627,467,343 — a signal ~1e-7 of the system.
- Runs were **deterministic**, so this is not Monte Carlo scatter.

### 4.3 There is currently no valid baseline

Two independent confirmations that the current code no longer reproduces the thesis:

| quantity | thesis | probe baseline (same config) |
|---|---|---|
| Lenacapavir @ 10% substitution | €346,408/QALY | **Dominated** (ΔQALY −36.9) |
| Oral PrEP vs no-PrEP | €122,477/QALY | **€683,630/QALY** |

Causes are plausible and expected — `demography.py` and `run_v3.py` both changed 2026-09-06 (ENG-01 rounding fix moved background deaths by +1,032 of 7,225,644), and `hta_runtime_config.json` may differ from the thesis run. **This is accepted, not a defect.** But the consequence must be drawn:

> **Every statement of the form "the ICER is X" or "Y does not move the answer" is currently measured against nothing.** CAL-01 and REL-01 (one production config, one frozen checksummed run) are now critical path, not Gate-5 cleanup.

### 4.4 Naming collision to fix before writing

The probe's "threshold price" (€49–€2,063) is *the price at which NMB vs oral PrEP flips positive when ΔQALY < 0* — effectively the price at which lenacapavir costs no more than oral PrEP. The thesis's €9,987 is *the price at which it is cost-effective at €80,000/QALY*. Same label, different quantities, 5–200x apart. Define both explicitly and never reuse the bare term.

### 4.5 The bifurcation

Sweeping the beta multiplier: at 11x, MSM diagnoses ~430/yr; at 12x, **exactly 0/yr**; above that, total (now all non-MSM) diagnoses climb smoothly 60 → 202 as the multiplier reaches 15.6x.

**Two readings:**

- *Real dynamics.* Two routes compete for a finite susceptible/contact pool; crank heterosexual transmission enough and it outcompetes and replaces the MSM epidemic. Genuine phenomenon in coupled-population models. Implication: the "matched" variant is **not** "our model with non-MSM fixed" — it is a different epidemic that replaced ours. Nothing was added; something was swapped.
- *Artifact (suspected).* Genuine competitive exclusion decays continuously (430 → 280 → 150 → 60 → 15 → 3 → 0). It does not jump to **exactly zero** in one multiplier step. "Exactly 0" means something is being *set* to zero, not falling to it. **Prime suspect: the same class of bug ENG-01 just fixed** — cellwise `np.round` dropping sub-0.5 expectations — but in the transmission path rather than `demography.py`. Once MSM per-cell expected infections fall below the rounding threshold in every cell, the sub-epidemic is deterministically annihilated in a single step.

**Why it matters beyond this stress test:** if confirmed, small sub-populations can be silently zeroed anywhere in the model. That is a live concern for every low-count stratum and a candidate contributor to §4.2.

**Fix if confirmed:** route the transmission path through the aggregate-preserving allocator already extracted into `stochastic_allocation.py` for ENG-01. The tool exists.

---

## 5. THE BLOCKING QUESTION

> **Can the model resolve the differences the CEA is asking it to measure?**

Status: **ANSWERED 2026-09-06 — NO, not for QALY-based quantities. See §15 for results and the revised plan. The contingency in §5.1 has fired.**

Original test specification, kept for reference:

1. **Seed variability.** Base case + one scenario across N seeds in stochastic mode; record spread of ΔQALY and infections averted. *Decisive test.* If between-seed spread is comparable to the between-scenario differences, the signal is under the noise floor. Also produces `V_ensemble` for §6 and probably explains the PSA tail draws (>€170B cumulative) flagged in the defense brief.
2. **Null-perturbation determinism check.** Same config, a change that provably cannot affect the outcome; confirm ΔQALY returns bit-identical. If not, there is a determinism leak.
3. **Fine multiplier sweep 11→12** in 0.1 steps. Smooth decline = dynamics; cliff = artifact (§4.5).

### 5.1 Contingency

| outcome | consequence |
|---|---|
| Spread small relative to scenario effects | Proceed with §9 as written. Timeline ~1–2 weeks. |
| Spread comparable to scenario effects | Engine integrity becomes the project. Fix allocation/rounding in the transmission path, re-verify, then restart §9. Timeline materially longer. Nothing downstream is interpretable until fixed. |
| Determinism leak found | Fix before anything else; all prior deterministic comparisons are suspect. |

**Do not start the exogenous non-MSM implementation until test 1 returns.** Its output would not be interpretable.

---

## 6. Method to adopt — Bayesian history matching

### 6.1 Why

Current gates mix two incompatible things: thresholds derived from published uncertainty (`outside_source_bounds`, which is fine) and thresholds that are simply chosen (`incidence_recent_ratio_ceiling = 1.75`, `plhiv_recent_mape_ceiling = 10.0`, which are not defensible under review). History matching replaces the chosen ones with derived ones and relocates judgement into a named, arguable variance term.

### 6.2 The implausibility measure

```
I(x) = |E[f(x)] − z| / sqrt( V_obs + V_code + V_ensemble + V_discrepancy )
```

| term | meaning | how to get it here |
|---|---|---|
| `V_obs` | observation uncertainty of the target | UNAIDS `Low/Mid/High` columns already in `DiseaseData.xlsx`; sigma ≈ (High − Low)/3.92 read as 95%. For SHM counts: ascertainment error, much smaller. |
| `V_code` | emulator variance | Zero if running the simulator directly rather than an emulator. Only needed if an emulator is introduced. |
| `V_ensemble` | the simulator's own stochastic variability | **Test 1 in §5 produces this.** Convention: mean or 95th percentile of sample variances. |
| `V_discrepancy` | the model is not reality | Stated and defended explicitly. Andrianakis et al. use 10% of the variance of simulator output training data per wave, declared openly as an illustrative choice. |

**Cutoff: rule out `I(x) > 3`**, justified by Pukelsheim's 3-sigma rule — any continuous unimodal distribution holds at least 93% of its mass within 3 standard deviations of the mean. The threshold is therefore distribution-free rather than chosen.

### 6.3 Why this is the right precedent

Andrianakis et al. (2015) apply exactly this to an **HIV transmission model for Uganda with 22 input parameters against 18 outputs**. This model has 27 parameters in `calib_space.json` (32 in `calib_space_v13_15_1975_orientation_seed_abc.json` — see §10, D-01a) against ~8 target series. Directly comparable scale and domain.

### 6.4 What it closes for free

Adopting history matching **resolves D-04 (posterior vs plausible-fit ensemble) automatically**. History matching *is* the method, so:

- "plausible-fit ensemble" / "non-implausible parameter space" is the correct language;
- every "posterior" and "credible interval" is dropped from code, figures, and manuscript;
- **SNPE can be cut from the manuscript entirely** — rounds still yield 0 posterior samples, three real bugs have been fixed without resolving it, and it has near-zero publication return. Sunk cost.

### 6.5 The overfitting argument, stated positively

Under **structural misspecification** — which is known here, the model generates zero heterosexual transmission — tightening calibration does not make the model more right. It shrinks the plausible set around a wrong region and reports it with false confidence.

This is the standard argument for history matching over full Bayesian calibration, and here it is a **positive methodological claim for the paper**, not a limitation to concede.

**Guard against tuning:** report **ensemble size as a function of the discrepancy term**. If the ensemble collapses to a handful of particles, the calibration is overfit; if it retains most of the prior, the targets are not informative. Showing where you sat on that curve is the evidence the tolerance was not chosen to pass.

### 6.6 Cost of widening tolerances — be conscious of it

Widening gates so more particles pass is not free. The plausible-fit ensemble gets wider, so the "threshold price is stable across the ensemble" claim (§8.3) becomes **harder** to make. That is a fine trade to make deliberately and a bad one to make accidentally. If widening is derived from uncertainty budgets, the resulting band is real; if tuned until gates pass, the band is an artifact of the tolerance choice.

---

## 7. Decided model direction — non-MSM as exogenous forcing

### 7.1 The decision

**Feed the observed SHM non-MSM diagnosis series (1996–2024) in directly as an exogenous forcing function.** Those people enter the PLHIV stock, consume ART and care costs, contribute to ART stock and AIDS deaths — but their incidence is **never endogenously generated**.

**Critically: this is NOT a migrant seeding exercise.** No migrant/domestic decomposition, no import volume parameter, no pre-2018 invented quantity. The original objection — that a `Migrant(1975–2017)` term would be unconstrained by data — stands and is respected by taking the *total* non-MSM diagnosis count as observed. This distinction is the entire reason the approach survives; do not let it drift.

Existing machinery: `migration.py` already injects pre-diagnosed people into the stock post-2018. The change is (a) extending back to 1996 using real data, and (b) routing them through `new_diagnoses` instead of bypassing it. A modest change to an existing pathway, not a new mechanism.

### 7.2 Why the forecasting problem is small — the cancellation argument

An exogenous stream is **identical in both arms**. Lenacapavir cannot change it by construction. So it contributes to each arm's totals but subtracts out of incremental costs and incremental QALYs — which is all the ICER sees.

**This argument is structural, not computed.** It holds regardless of how §5 resolves, because it is a statement about model design rather than a model output. That makes it one of the few pieces of solid ground currently available.

Caveat for honesty: cancellation is exact in the idealised model and approximate in the implementation, since rounding interactions can perturb a specific deterministic run.

### 7.3 Post-2024 extrapolation options

| option | basis | role |
|---|---|---|
| Freeze at recent level | 2020–24 are flat: 168/171/198/212/199 | **base case** — needs no trend assumption |
| Scale to net migration | CBS `positive_net_migration` + `NL_Population_Forecasts.csv` | alternative scenario, semi-mechanistic |
| Extrapolate 2004–2020 decline | the long decline | alternative only — trend reversed after 2020, so trend choice is a judgement call |

Report the base case and both alternatives, with §7.2 explaining why the spread is small. **If it is not small, that is a finding to know before writing.**

### 7.4 Anticipated reviewer question

*If they cancel, why include them at all?* — For the epidemiological figures, denominators, and total-cost reporting. Model credibility, not decision content. State this plainly rather than implying the non-MSM stratum does analytic work it does not.

### 7.5 Known open issue with this design

Making non-MSM exogenous means endogenous incidence becomes MSM-only, while the UNAIDS `new_infections` target is all-group. Resolution options: compare against an MSM-specific incidence estimate, or add the exogenous stream back with an assumed infection-to-diagnosis lag. **Confirm tractable before building.**

---

## 8. Manuscript decisions

### 8.1 Bias-direction table (as a table, not prose)

| Assumption | Effect on estimated lenacapavir benefit | Direction on ICER |
|---|---|---|
| Non-MSM exogenous → no MSM→partner bridging credit | Understates infections averted | Against lenacapavir |
| Model incidence ~2.7x CSAVR estimate | Overstates avertible infections | Favours lenacapavir |
| Productivity losses disabled | Understates averted societal costs | Against lenacapavir |
| Non-MSM excluded from transmission pool | Understates epidemic size | Ambiguous |

Closing sentence: *the two largest biases act in opposite directions, and the conclusion holds under either.* Not a claim that they cancel — a claim that the result survives both.

**Bridging, defined for the text:** some MSM also have sex with women. In a model with that pathway, preventing an infection in an MSM also prevents onward transmission to his female partners — a "bridge" between the two epidemics. Under exogenous forcing that pathway is off, so lenacapavir receives no credit for infections it would have averted there.

### 8.2 Scope-outs, with the decision rule

**Rule: model what the decision needs, plus what the figures need in order not to be false. Nothing else.**

| mechanism | decision effect | figure effect | verdict |
|---|---|---|---|
| Non-MSM | none directly | 38% of a series we claim to reproduce | **model it** (exogenously) |
| PWID | none (near-zero in NL today) | plausibly part of the missing 1990s AIDS peak | scope out + disclaimer |
| Sex work | none | absorbed into the observed non-MSM series | scope out |
| Geography / regional | none | none | scope out |

PWID disclaimer content: historically significant in 1980s–90s Amsterdam, driven to near-zero by Dutch harm reduction. Flag as a stated reason the historical fit under-represents the pre-ART era. **The claim that PWID absence contributes to the missing AIDS peak is a HYPOTHESIS, unverified** — check against the ECDC AIDS series before asserting it in print.

### 8.3 Framing — lead with the threshold price

"The price at which lenacapavir becomes cost-effective in the Netherlands" is more citable, more decision-relevant, and structurally more stable than an absolute ICER, because a ratio lets calibration error largely cancel between arms.

**The strongest available claim:** if the threshold price sits in a tight band across the plausible-fit ensemble while the raw ICER swings, that is simultaneously the headline result and the answer to every calibration criticism. It converts the weakest point into the methodological contribution.

### 8.4 Historical-period honesty

1975–1989 has **zero targets** (UNAIDS starts 1990, ECDC starts 1990, SHM's earliest is a `<=1995` lump, and `scoring_start_year = 1990`). The manuscript must say **"35 scored years plus burn-in"**, not "50 years of Dutch HIV history."

Additionally, the `<=1995` bucket is cohort-reconstructed from people who later entered care, so it systematically under-ascertains those who died pre-ART or never entered care. **Do not lean on pre-1996 fit** — it is a biased target.

### 8.5 Still the top manuscript weak point — unchanged since the defense brief

**The societal-perspective label (HTA-01).** The base case is labelled societal but productivity and informal care are disabled. This is a **CHEERS-level reporting violation** that can produce a desk rejection rather than a review comment. Preferred resolution: relabel to "healthcare payer plus patient time costs" and run productivity as a sourced sensitivity. Supporting facts already resolved and in `hta_costing_config.json`: patient time wage €39.88/hr (iMTA 2024 Table 6-2), friction period 16.4 weeks (iMTA 2024 Table 6-1), and the Tuiu et al. (2026) crosswalk gap that prevented base-case activation.

### 8.6 Also required before submission

- **Check the price assumption is current.** The €41,110/yr list price and the EU access/pricing landscape move fast. A stale price makes the headline result stale.
- **Literature scan for lenacapavir CEAs published since June 2026** — changes the novelty claim.
- **One paper or two?** A bespoke 1975–2025 transmission model plus a full CEA in one health-econ paper pushes the model description into supplementary material, where calibration weaknesses become invisible rather than resolved. Splitting (methods/epi paper, then CEA) is cleaner but slower. Decide early — it changes how much of §9 is needed.

---

## 9. The plan

### Phase 0 — Unblock (running now)

- [ ] **P0-1** Seed-variability test → `V_ensemble` and the noise-floor answer (§5.1)
- [ ] **P0-2** Null-perturbation determinism check
- [ ] **P0-3** Fine multiplier sweep 11→12 (bifurcation vs artifact)
- [ ] **P0-4** Decision point: proceed, or divert to engine integrity

### Phase 1 — Foundation (no interpretable result exists without this)

- [ ] **P1-1** Resolve `calib_space.json` (27 params) vs `calib_space_v13_15_1975_orientation_seed_abc.json` (32 params) — which is canonical? (CAL-01, CAL-03)
- [ ] **P1-2** Declare ONE production config; remove diagnostic/rank001/rank012/model_final ambiguity (CAL-01)
- [ ] **P1-3** Freeze and checksum a reference run + manifest (REL-01, R0-06)
- [ ] **P1-4** If P0-3 says artifact: apply the `stochastic_allocation.py` allocator to the transmission path, re-verify, refreeze
- [ ] **P1-5** Decide production start year (D-01/CAL-02) — note 1975–1989 is unscored burn-in either way

### Phase 2 — Method (partly parallel with Phase 1)

- [ ] **P2-1** Write the target-tiering rule: observed counts vs CSAVR-derived estimates (§3.4, §6.2)
- [ ] **P2-2** Implement the implausibility measure; derive `V_obs` from published bounds, `V_ensemble` from P0-1
- [ ] **P2-3** Specify and justify `V_discrepancy`; produce the ensemble-size-vs-discrepancy curve (§6.5)
- [ ] **P2-4** Retire arbitrary thresholds (`incidence_recent_ratio_ceiling`, `plhiv_recent_mape_ceiling`, etc.)
- [ ] **P2-5** Record D-04 as "history-matched plausible-fit ensemble"; purge posterior/credible-interval language; cut SNPE from the manuscript
- [ ] **P2-6** Move UNAIDS incidence/PLHIV to cross-validation comparators; write the ISPOR-SMDM cross-validation section

### Phase 3 — Model change and analysis

- [ ] **P3-1** Implement exogenous non-MSM forcing from the SHM series 1996–2024 (§7.1)
- [ ] **P3-2** Resolve the MSM-only-incidence vs all-group-target issue (§7.5)
- [ ] **P3-3** Build and freeze the plausible-fit ensemble (CAL-07)
- [ ] **P3-4** **Multi-fit robustness: run the CEA across the whole ensemble; report threshold-price spread** (REL-03) — highest-value single analysis in the project (§8.3)
- [ ] **P3-5** Complexity ablation: run the CEA with a static/simplified population; quantify what the demographic scaffolding bought. Reviewers will ask; you need a number.
- [ ] **P3-6** Pre-specified structural sensitivities: event ordering (HTA-04, epi delta known ~1–2%, ICER delta outstanding), transmission form (ENG-02), horizon (HTA-03), discount rates, perspective
- [ ] **P3-7** Cross-model validation against Nichols et al. 2016 and any published lenacapavir CEA

### Phase 4 — Manuscript (NO CODE DEPENDENCY — start immediately, in parallel with Phase 0)

- [ ] **P4-1** Resolve the perspective label (HTA-01) — relabel + productivity sensitivity (§8.5)
- [ ] **P4-2** Write the bias-direction table (§8.1)
- [ ] **P4-3** Write the scope-out paragraph: PWID, sex work, geography, with the decision rule (§8.2)
- [ ] **P4-4** Fix the "threshold price" naming collision; define both quantities (§4.4)
- [ ] **P4-5** Correct historical-period claims to "35 scored years plus burn-in" (§8.4)
- [ ] **P4-6** Price-assumption currency check + lenacapavir CEA literature scan (§8.6)
- [ ] **P4-7** Target journal decision + one-paper-or-two decision (§8.6)

### Phase 5 — Freeze and release

Per checklist Gate 5 (REL-01..REL-10), plus: resolve the 59 test failures / 5 errors, and the git state (single commit, ~10 uncommitted files; note the ENG-01 completion-log entry incorrectly reads "no git repo").

### Sequencing note

**Phase 4 has zero code dependency and should start immediately**, regardless of how Phase 0 resolves. Do not let engine work block the writing decisions — several of them (perspective label especially) are higher-severity for publication than anything in Phases 1–3.

---

## 10. Decisions still to record

Map into the checklist Decision Log.

| ID | Decision | Status |
|---|---|---|
| **D-01a** | Canonical calib_space: `calib_space.json` (27) vs `..._v13_15_1975_orientation_seed_abc.json` (32) | **NEW — open** |
| D-01 | Canonical production start year | Open; note 1975–1989 is unscored either way |
| D-02 | Fixed vs calibrated natural-history parameters | Open |
| D-03 | Non-MSM strategy | **Direction agreed: exogenous observed forcing (§7.1). Formalise.** |
| D-04 | Posterior vs plausible-fit ensemble | **Resolved by adopting history matching: plausible-fit ensemble (§6.4)** |
| D-05 | Primary HTA perspective | Open — highest manuscript severity (§8.5) |
| D-06 | Primary evaluation horizon | Open |
| **D-07** | Target epistemic tiering + discrepancy specification | **NEW — open (§6.2, §3.4)** |
| **D-08** | Post-2024 non-MSM extrapolation rule | **NEW — open (§7.3)** |

---

## 11. Reading list

### Priority 1 — the method to adopt

- **Andrianakis I, et al. (2015). "Bayesian History Matching of Complex Infectious Disease Models Using Emulation: A Tutorial and a Case Study on HIV in Uganda." *PLoS Computational Biology* 11(1):e1003968.**
  https://journals.plos.org/ploscompbiol/article?id=10.1371%2Fjournal.pcbi.1003968
  *Read for:* the implausibility formula, the four variance components, the I > 3 cutoff and its Pukelsheim justification, how model discrepancy is specified in practice, and the wave-based workflow. 22 params / 18 outputs on an HIV model — the closest published analogue to this project.

- **Andrianakis I, et al. "History matching of a complex epidemiological model of HIV transmission by using variance emulation." *JRSS-C*.**
  https://ncbi.nlm.nih.gov/pmc/articles/PMC5516248
  *Read for:* handling stochastic simulator variance specifically — directly relevant to the §5 question.

### Priority 2 — why UNAIDS targets are not independent evidence

- **Mahiane SG, et al. (2021). "Updates to Spectrum's case surveillance and vital registration tool for HIV estimates and projections." *Journal of the International AIDS Society*.**
  https://pmc.ncbi.nlm.nih.gov/articles/PMC8454676/
  *Read for:* what CSAVR is, that NL is among the TESSy reporters using it, what inputs it takes (case-surveillance diagnoses + vital-registration deaths), and that it is a Bayesian fit rather than a projection. This is the citation for §3.4.

- **UNAIDS, "Methods for deriving UNAIDS HIV estimates" (Annex 1).**
  https://www.unaids.org/sites/default/files/2025-08/JC3153_GAU25_report_annex1_en.pdf
  *Read for:* the official methods statement to cite alongside the above.

- **Estimating and projecting new HIV diagnoses and incidence in Spectrum's CSAVR tool**, *AIDS*.
  https://doi.org/10.1097/qad.0000000000002324

### Priority 3 — validation and reporting standards

- **Eddy DM, et al. (2012). ISPOR-SMDM Modeling Good Research Practices Task Force-7: "Model Transparency and Validation." *Value in Health*.**
  *Read for:* the five validation types — face, internal/verification, cross-validation, external, predictive. The project currently has internal and partial external; §P2-6 and §P3-7 fill cross-validation.

- **CHEERS 2022 statement.**
  *Read for:* the reporting checklist most health-econ journals require. Relevant to §8.5 — perspective mislabelling is a checklist violation, not a limitation.

### Priority 4 — background on the approach

- **Craig PS, Goldstein M, Seheult AH, Smith JA — foundational history-matching / implausibility work.** Background for why the method exists; Andrianakis is the applied entry point.
- **Brookmeyer R, Gail MH (1988) — HIV back-calculation.** Background for why estimating a latent input from an observed output series is standard practice in this field (relevant to the §7 framing).
- **Kennedy MC, O'Hagan A (2001), "Bayesian calibration of computer models," *JRSS-B*.** The full-Bayesian alternative with explicit discrepancy modelling — heavier than needed here, but the reference point for why history matching was preferred.

---

## 12. Rejected paths — do not relitigate

| Path | Why rejected |
|---|---|
| **Fix non-MSM by adjusting transmission betas** | Requires 11–40x past the literature 95% CI and tips the epidemic (§3.5). Quantified and closed. |
| **Model migrant HIV import as a mechanistic pathway with a pre-2018 term** | The pre-2018 import volume is not observable; the term would be unconstrained. User objection stands. Superseded by exogenous forcing on the *total* non-MSM series, which requires no decomposition (§7.1). |
| **Add mechanisms: sex work, PWID, regional/geographic weighting, finer network structure** | None changes the decision; none fixes a false figure. The model is under-identified, not under-mechanised (§8.2). |
| **Continue SNPE** | Rounds still yield 0 posterior samples after three real bug fixes. Near-zero publication return. Cut from the manuscript (§6.4). |
| **Scope the model down to MSM-only** | Viable and was seriously considered, but exogenous forcing achieves the same gate resolution at similar cost while keeping the national denominator. Keep as fallback if §7 proves intractable. |
| **Use the thesis numbers as a baseline** | Code has changed; two independent quantities no longer reproduce (§4.3). |

---

## 13. Resuming in a new session

Paste roughly this:

> I'm working on the Phase 2 manuscript conversion of my MSc thesis, "Cost-Effectiveness of Lenacapavir for HIV PrEP versus Oral PrEP in the Netherlands." Read `hiv-prep-model/PHASE2_STRATEGY_BRIEF_2026-09-06.md` first — it has the full state, decisions, and plan. Then read `hiv-prep-model/PHASE2_DEVELOPMENT_CHECKLIST.md` for the operational gates. The blocking question is §5 (numerical resolution of the incremental analysis); check whether those three tests have returned before proposing work downstream of them. Project instructions apply: label claims as unsupported / weakly supported / well-supported, never invent parameter values, keep model assumptions, empirical inputs, derived results, and interpretation strictly separated.

---

## 14. Session provenance

Derived from the 2026-09-06 strategy session. Evidence base: `TARGET_REACHABILITY_AUDIT.md` (2026-09-06), `NON_MSM_DECISION_RELEVANCE_PROBE.md` (2026-09-06), `abc_smc_particles_20260604_065622.csv`, `V13.22_20260604_nearfit_rank001_basecase_PSAReady.json`, `DiseaseData.xlsx`, `calib_space.json`, `calib_acceptance_gates.py`, `PHASE2_DEVELOPMENT_CHECKLIST.md`, plus the two literature sources verified in §11 Priority 1–2.

**Claims in this document not yet verified, explicitly flagged:**
- The numerical-resolution problem (§4.2, §5) — *hypothesis, three tests running*.
- The bifurcation as rounding artifact (§4.5) — *hypothesis, test P0-3 pending*.
- PWID absence as a contributor to the missing AIDS peak (§8.2) — *hypothesis, unverified against the ECDC series*.


---

## 15. Phase 0 results (2026-09-06) — the blocking question is answered

**Report:** `outputs/hta_results/probe_diagnostics_20260906/PROBE_DIAGNOSTICS.md`

### 15.1 Headline

**The QALY layer cannot resolve the differences the CEA asks of it. The epidemiological layer can.** This is a bounded, localised problem — not a whole-engine failure — but it invalidates ΔQALY-based results from the current pipeline until fixed.

### 15.2 Deterministic mode does not reproduce the stochastic mean — for QALYs only

15 stochastic seeds (42–56), rank001 base case:

| scenario | det ΔQALY | stoch mean ΔQALY | stoch SD | SE (n=15) |
|---|---|---|---|---|
| Base case (oral PrEP vs no PrEP) | **854.6** | **25,358.7** | 41,917.9 | 10,824 |
| Lena substitution switch_10 | −36.9 | −23,089.9 | 36,334.7 | 9,382 |
| Lena substitution switch_100 | −1,827.4 | −13,234.8 | 45,025.3 | 11,626 |
| Oral PrEP expansion benchmark | **+1,252.1** | **−21,678.7** | 30,090.0 | 7,769 |

Contrast the same comparison's epidemiological metric:

| scenario | det infections_averted | stoch mean | stoch SD | SE (n=15) |
|---|---|---|---|---|
| Base case (oral PrEP vs no PrEP) | **827** | **882** | 398 | 103 |

**`infections_averted` agrees between modes (827 vs 882 ± 103). `ΔQALY` does not (855 vs 25,359 ± 10,824), and for the oral-PrEP-expansion benchmark the two modes disagree in sign (+1,252 vs −21,679 ± 7,769).**

This is the key result, and it does **not** support the interpretation that determinism is simply "the right tool" for isolating the signal. Determinism buys reproducibility, not accuracy. If deterministic mode were computing the expected trajectory, the deterministic ΔQALY would approximate the stochastic mean. It does not — off by ~30x in one case and sign-flipped in another (each ~2.3–3.0 SE, so *strongly suggestive but not conclusive at n=15*).

### 15.3 Noise-to-signal, quantified

ΔQALY between-seed SD is 26,700–45,000. The largest deterministic ΔQALY across the whole earlier non-MSM probe was −1,827; the full spread across all variants and switch levels never exceeded ~2,700.

Seeds required to resolve a signal of size Δ at SE = Δ/4, with SD ≈ 35,000:

| target signal | n seeds needed |
|---|---|
| 2,700 (largest deterministic effect) | ~2,700 |
| 166 (between-variant differences in the non-MSM probe) | ~710,000 |

The between-variant differences the earlier probe rested on are **unresolvable by any feasible amount of stochastic simulation.**

### 15.4 Mechanism — located in code

`scenario_comparator.py:186`
```python
delta_qaly = intervention.total_qalys_disc - reference.total_qalys_disc
```

`economics.py:450–480` accumulates QALYs across the **entire population**:
```python
qalys += susc_no_prep * u_susc * step_fraction    # all susceptibles, ~10-17M people
qalys += count * u_prep  * step_fraction
qalys += count * u_hiv   * step_fraction
```

So ΔQALY is the difference of two grand totals of order 10^7–10^8, to recover a true difference of order 10^3.

**Hypothesised mechanism (to be confirmed by 15.6):** the arms fork from a frozen 2025 baseline and then simulate the *whole* population independently. Background mortality, aging, births and migration are redrawn per arm and are unrelated to the intervention, but they move `total_qalys_disc` far more than the intervention does. In deterministic mode, a handful of rounding differences in a ~10M-person susceptible pool each contribute ~1 QALY/yr × decades discounted ≈ tens of QALYs — comparable to or larger than the entire intervention effect. `infections_averted` is well-conditioned (a small count of a rare event computed directly) which is why it behaves correctly in both modes.

Note this is the same failure family as ENG-01 (cellwise rounding dropping rare events), now appearing in the economics layer rather than `demography.py`.

### 15.5 Consequences

- **The earlier non-MSM decision-relevance probe's conclusion cannot be cited.** Its Path-B finding (§3.5) is unaffected and remains solid — that rests on non-MSM diagnosis counts, not QALYs.
- **The thesis's ΔQALY-derived numbers may be substantially artifact.** Not established, but no longer safe to assume otherwise.
- **The stochastic PSA is not usable off the current pipeline.** If each draw carries ~35,000 QALYs of Monte Carlo noise, the PSA output is dominated by simulation noise rather than parameter uncertainty. **This fully explains the >€170B tail draws** flagged in the defense brief — one root cause, not a separate oddity. Any CEAC built on it is meaningless.
- **Not affected:** the transmission model, the demographic scaffolding, `infections_averted`, and any cost-per-infection-averted framing.

### 15.6 Next tests (replaces old P0)

- [ ] **P0-5 — Variance decomposition of ΔQALY by health state.** Decompose `total_qalys_disc` into susceptible / on-PrEP / HIV-state components and compare arms. If the susceptible term — which should be near-identical between arms — carries most of the difference and most of the variance, 15.4 is confirmed. **Decisive and cheap.**
- [ ] **P0-6 — Differenced accumulation.** Re-express ΔQALY as `Σ_t Σ_state (n_intv[t,state] − n_ref[t,state]) × u[state] × discount[t]` — algebraically identical, far better conditioned, because per-step differences are small numbers rather than differences of large ones. Compare against the current computation.
- [ ] **P0-7 — Common random numbers across arms.** Required for any stochastic PSA. Share random streams between arms for everything the intervention does not affect; the hard part is keeping streams synchronised when the intervention changes how many draws are consumed. Standard variance-reduction technique for simulation-based CEA.
- [ ] **P0-8 — Re-run seed variability at n = 40–50** once P0-6 lands, to confirm the deterministic-vs-mean discrepancy and tighten `V_ensemble`.

### 15.7 Tests 2 and 3 — resolved

- **Null-perturbation determinism check: clean pass.** Two full runs differing only in an unrelated scenario's target; every other row bit-identical including all of `scenario_a` and its price sweep. No determinism leak, no cross-scenario contamination.
- **The 11→12 bifurcation is real dynamics, not a rounding artifact.** The artifact hypothesis in §4.5 is **disconfirmed**. Tracking `plhiv_male_15plus` back to 1980 shows the two trajectories diverge smoothly and monotonically from the first simulated year (ratio 0.906 in 1980 → 0.119 by 2017) — ordinary compounding exponential sensitivity over ~40 years. The apparent cliff exists only in the integer diagnosis count, which floors at zero once the shrunken sub-epidemic's expected annual diagnoses fall below 1. **Residual point that still stands:** `new_diagnoses_msm` is a misleading diagnostic near extinction, and the §3.5 caveat on the "matched" variant holds either way.
- **Secondary observation:** a 0.9% change in the beta multiplier (11.10 → 11.20) produces an ~8x difference in PLHIV by 2017. That is extreme sensitivity — good for identifiability, poor for stability — and it strengthens the §8.4 argument about the unscored 1975–1989 burn-in, since 15 unconstrained years feed 45 years of compounding.

### 15.8 Revised plan impact

The §5.1 contingency "spread comparable to scenario effects" has fired, with the important refinement that **the problem is localised to the QALY-accumulation layer, not the engine as a whole.**

- **Phase 0 extends** with P0-5 → P0-8 before Phase 1 freezing is meaningful (freezing a run whose ΔQALY is artifact would freeze the artifact).
- **Phase 1 (P1-1, P1-2, P1-5)** — config canonicalisation and the calib_space question — is independent of the QALY layer and can proceed in parallel.
- **Phase 2 (calibration methodology)** is unaffected: history matching operates on epidemiological targets, which are sound. Proceed as written. `V_ensemble` for epidemiological targets from §15.2 is usable now.
- **Phase 3 gating:** P3-4 (multi-fit robustness) and P3-5 (complexity ablation) both rest on ΔQALY and must wait for P0-6. **P3-7 (cross-model validation) is epidemiological and can proceed.**
- **Phase 4 (manuscript) is entirely unaffected** and remains the highest-value parallel track. The perspective label (P4-1) does not depend on any of this.
- **Interim framing option:** `cost_per_infection_averted` is well-conditioned in both modes and could carry the analysis while the QALY layer is repaired. Not a substitute for a CEA, but usable for internal decisions and possibly as a secondary reported outcome.
