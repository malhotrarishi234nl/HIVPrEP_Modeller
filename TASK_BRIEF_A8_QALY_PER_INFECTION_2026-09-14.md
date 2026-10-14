# Task brief — A-8 (revised): is 1.9 discounted QALYs per averted infection plausible?

**Created:** 2026-09-14 · **Owner:** Rishi Malhotra
**Type:** derivation and literature check. **Implements nothing.** Ends in a judgement on whether the model's per-infection health consequence is defensible, and what drives it.
**Supersedes:** the A-8 entry in `PHASE2_AGENT_HANDOFF_2026-09-09.md` (framed on pre-CRN figures that no longer apply — see §2).
**Batching:** independent of A-24 — different question, different outputs. Can run in parallel.
**Model runs:** none expected. If the distribution of averted infections by year/age/group cannot be recovered from retained A-2 outputs, one instrumented CRN-paired run at seeds 42–56 **may be executed by the agent** (foreground, smoke first). No calibration runs.

---

## 1. The question

A-2 (`outputs/hta_results/a2_estimator_decomposition_20260913_235517/`, CRN-paired stochastic, n=15) measured the health consequence per averted infection exactly:

| per infection averted (discounted) | mean | SD | min | max |
|---|---:|---:|---:|---:|
| LY via HIV deaths averted | 1.328 | 0.181 | 1.026 | 1.627 |
| HIV-state utility weight | 0.521 | 0.017 | 0.490 | 0.551 |
| ART-initiation decrement | 0.062 | 0.009 | 0.051 | 0.083 |
| **infection channel** | **1.911** | **0.182** | 1.610 | 2.236 |

Undiscounted: 2.816 (SD 0.263). SE of the discounted mean at n=15 ≈ **0.047**.

**Is 1.911 what the model's own parameters imply, and is it what the published evidence implies once aligned to this model's horizon and discounting?** Those are two separate questions and must be answered separately.

---

## 2. What has changed since the original A-8

- The original A-8 compared **42.9** stochastic person-years per infection against **0.62** deterministic. The 42.9 was pre-CRN Monte Carlo noise (A-4 cut the SD 97%). **Do not use either number.**
- The thesis figure of **4.20** QALYs per infection was an artefact of cellwise rounding of background deaths (A-11). **Not a benchmark.**
- Deterministic mode gives **1.03** total (854.6 / 827). **It is not a valid comparator**, because four completed tasks have now shown deterministic integer allocation distorts exactly the quantities this figure is built from:
  - **A-13:** cross-stage residual assignment in diagnosis/ART calls; deterministic averts −41 HIV deaths vs stochastic −100 (12.4 SE).
  - **A-21 / A-22:** deterministic background mortality under-allocates HIV-positive background deaths by **22.05%** over 1975–2025 at the production grouping (6,259 allocated vs 8,029.7 expected; 97.3% in 1975–1990). No grouping level tested by A-22 is material, so D-14 remains open (`outputs/calibration/a22_background_mortality_grouping_20260913_225630/`, canonical).
  - **A-23:** deterministic HIV mortality over-allocates deaths to MSM and starves non-MSM (24.5% of expected); the +122% level effect runs through the transmission feedback (`outputs/calibration/a23_hiv_mortality_anomaly_20260913_235530/`).
  - **A-9:** no coherent deterministic repair for the substitution grid; its recommendation is to report that grid from stochastic runs only.
  Report 1.03 for context only, and do not attempt to reconcile it.
- **The stochastic infection channel (1.911) is the figure under test.** This task is entirely on the stochastic path and the cohort derivation, so **it is not blocked by D-14**.
- Two "plausible ranges" are circulating and **both are unsourced**:
  - "5–15 discounted QALYs" — A-2 brief. A-2 searched `literature/` and found no support.
  - "roughly 2–5" — handoff (~line 392), explicitly a derived estimate never verified.
  **Treat both as hypotheses. Neither may be cited as evidence.**

---

## 3. Read first

1. `PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A, the A-8, A-11 and A-13 entries.
2. `outputs/hta_results/a2_estimator_decomposition_20260913_235517/A2_ESTIMATOR_DECOMPOSITION.md` §2, §4, §5.1 — and `run_full/` (per-seed and per-step outputs).
3. `outputs/hta_results/a11_causal_flow_20260910_044500/A11_QALY_GAP_LOCALISATION.md`.
3a. For context on why deterministic figures are excluded: A-22 report §2–3 (`outputs/calibration/a22_background_mortality_grouping_20260913_225630/`), A-23 report §0 and §6, A-9 report §1. Skim, do not re-analyse.
4. `economics.py` — exactly how QALYs and discounting accumulate (timing convention, mid-step vs end-step, base year).
5. `configs/hta_costing_config.json` — `discounting` (health 1.5%/yr, base year 2025, ZIN 2022) and `utilities` (chronic on ART 0.98 normalised from Popping 2021; chronic off ART 0.78; AIDS states; ART-initiation decrement).
6. `configs/hta_config.json` / `configs/hta_runtime_config.json` — 40-year horizon from 2025.
7. `configs/V13.22_20260604_nearfit_rank001_basecase.json` — progression, HIV mortality by stage and ART status, diagnosis, ART initiation/dropout, background mortality.

---

## 4. Step 1 — derive the model-implied value independently. No simulation.

Build a cohort (Markov) calculation, in a standalone script, using **only the model's own parameters** read from the configs above:

- One infection at age *a*, group *g*, calendar year *t*, followed through the model's stage structure (acute → chronic → AIDS; undiagnosed/diagnosed; off/on ART) with the configured transition, diagnosis, ART and mortality rates, and the model's background mortality.
- Counterfactual: the same person uninfected (utility 1.0, background mortality only).
- **Background mortality for HIV-positive people.** A-22 states every level uses "the same unchanged life-table mortality probability for every person", i.e. HIV-positive people carry general-population background mortality **plus** stage-specific HIV mortality. Confirm this in `demography.py` / `run_v3.py` and use the model's actual structure — not the deterministic allocation's realised rates, which A-21/A-22 show are distorted. Use the continuous probabilities the stochastic path draws on.
- **Competing risks.** A-2 found a resolved background-death LY component of −606.7 partly reflecting people saved from HIV death later dying of other causes. The cohort derivation handles this by construction; state how much of the gap between "LY via HIV deaths averted" (1.328) and the cohort's net LY gain is that competing-risk echo.
- **Quantity 1 — horizon-truncated:** discounted QALY loss from *t* to 2064 at 1.5%, discounting to 2025 with `economics.py`'s exact convention. This is what the model's 1.911 measures.
- **Quantity 2 — lifetime:** same, to death, discounted to the year of infection. This is what published per-infection figures usually measure.
- Split each into LY lost via HIV death, utility shortfall, and ART-initiation decrement, matching A-2's channels.

**Weight by the actual timing and mix of averted infections.** Recover the distribution of infections averted by year (and by age/group if available) from retained outputs before running anything:
- A-2's `run_full/` (base case, seeds 42–56);
- A-9's `runs/all_stochastic/seed_{42..56}/timestep_simulation_deltas.csv` has per-step `delta_new_infections` — but for the **substitution** comparison (lenacapavir vs oral PrEP), not the base case. Use it only as a cross-check on timing shape, not as the base-case weights.

Horizon truncation matters: an infection averted in 2055 contributes about ten years. If the base-case distribution by year is not retained, run the one instrumented CRN-paired run allowed above. Age/group mix: if not retained, state the assumption and run the derivation under its plausible range.

**Pre-specify before computing (write `PRESPECIFICATION.md` first, timestamped):**
- The derived value is deterministic but has **approximation error** (cohort vs compartmental dynamics, time-step aggregation, age-bin midpoints). Quantify it by running the derivation at two step sizes and two age-bin conventions; the spread is the approximation band.
- **Agreement** if |derived − 1.911| ≤ 2 × 0.047 **plus** the approximation band. State the band's numerical size in the same sentence as the criterion.
- **Caveat to state up front:** A-2's "per infection averted" divides population-level QALY gains by all infections averted, including secondary ones and those averted late. The cohort derivation counts direct consequences only. Say which way that difference should bias the comparison before computing it.

---

## 5. Step 2 — find a real published comparator

**First `literature/`.** A-2 checked Nichols 2016 (+ supplement), Wang 2025, NICE TA1106 ×2, Trickey 2017, Popping 2021/2023 and found no direct per-infection lifetime QALY loss. Do not repeat that search blindly — instead look for figures from which one **can be derived**:
- life expectancy with HIV on ART vs general population (Trickey 2017; ATHENA / Boender 2018; Stichting HIV Monitoring 2025 report);
- lifetime QALYs or QALYs lost per infection hidden in model supplements (Nichols 2016 supplement, Kuipers 2023 supplement, RIVM 2023, NICE TA1106 committee papers);
- HRQoL on ART (Popping 2021, Miners 2014, Tengs 2002, van der Kolk 2010, Treskova 2021).

**Then external sources.** Web search is permitted for published estimates of **lifetime QALYs lost (or gained by prevention) per HIV infection** in high-income, ART-era settings, preferably Netherlands/Western Europe. Rules:
- Every figure needs author, year, journal, DOI or stable URL, table/page, and the exact definition (discounted? rate? horizon? age at infection? population?).
- Verify each against retrieved text; **never cite from memory or a search snippet alone**.
- **Do not download files.** List any source that should be added to `literature/` in a "sources to acquire" table for Rishi.
- If no directly comparable figure exists, say so — that is a finding.

---

## 6. Step 3 — harmonise and attribute the gap

For each comparator, convert to this model's basis: 40-year horizon from 2025, 1.5% health discounting to 2025, the model's age/timing distribution of averted infections, utility scale with HIV-negative = 1.0.

Then attribute the difference between the harmonised comparator and 1.911, additively, to:
1. horizon truncation;
2. discount rate and discounting base;
3. survival on ART (model mortality vs comparator);
4. utility on ART (0.98 normalised vs comparator);
5. stage mix / late diagnosis;
6. residual.

The attribution must sum to the total gap. **If it does not, the harmonisation is wrong — fix before reading it.**

---

## 7. Step 4 — judgement

State plainly:
- whether 1.911 is what the model's parameters imply (Step 1);
- whether it is consistent with the harmonised evidence (Steps 2–3), and if not, which named inputs drive the gap;
- which inputs a sensitivity analysis must cover for the thesis, with the plausible range for each **and its source** — but do not run the sensitivity analysis;
- the implication for the deterministic figure (1.03), given A-13, A-21/A-22 and A-23 — descriptively only;
- **scope note:** this task covers the base case (oral PrEP vs no PrEP). The substitution grid's per-infection ratios are dominated by the lenacapavir AE decrement and a near-zero denominator at switch 10 (A-2 §5.1; A-9 stochastic reference 0.67 infections averted at switch 10). Do not extend the plausibility judgement to them;
- whether this is a disclosure item, a sensitivity item, or a defect.

---

## 8. Acceptance

- [ ] `PRESPECIFICATION.md` written before any derivation result is read.
- [ ] Horizon-truncated and lifetime derived values reported, each split into the three channels.
- [ ] Approximation band quantified; agreement criterion stated with its resolution in the same sentence.
- [ ] Derived vs 1.911 verdict stated.
- [ ] Every comparator fully cited and verified; unsourced ranges (5–15, 2–5) explicitly not used as evidence.
- [ ] Harmonisation attribution sums to the total gap.
- [ ] Judgement and sensitivity-input list, with sources.
- [ ] "Sources to acquire" table.
- [ ] Full suite run only if any code was added outside the output directory (it should not be); `git diff` on tracked files unchanged (hash `7f034cf1…c166` at last check), or explained.
- [ ] Limitations: one parameter point, cohort approximation, comparator definitions.

## 9. Escalate immediately if

- **The derived value disagrees with 1.911 beyond the pre-specified criterion.** That means the simulation does not do what its parameters say — an accounting defect in `economics.py` or the model, more serious than a plausibility question. Stop and report.
- **The harmonised published evidence differs from 1.911 by more than 3×** and the gap attribution points to a model input. That affects the thesis's central health-outcome claim and is Rishi's call.
- **No usable comparator exists.** Say so; do not substitute a heuristic.

## 10. Do not

- Do not change any utility, mortality, progression, discount rate or horizon — these are exactly the parameters that would "fix" a plausibility gap while hiding its cause (§1A.4).
- Do not compute or report an ICER.
- Do not use the thesis's 4.20, the pre-CRN 42.9, or either unsourced range as evidence.
- Do not duplicate A-24 (births/CRN drift) or A-2's decomposition; reuse A-2's outputs. A-24 may conclude that part of the non-HIV variance is a CRN artefact; that does not affect the infection channel's mean (1.911), which is what A-8 tests.
- Do not use any deterministic-mode quantity as an input to the derivation (A-13, A-21/A-22, A-23).
- Do not download files or cite unverified sources.
- Write only into `outputs/hta_results/a8_qaly_per_infection_<YYYYMMDD_HHMMSS>/`.
