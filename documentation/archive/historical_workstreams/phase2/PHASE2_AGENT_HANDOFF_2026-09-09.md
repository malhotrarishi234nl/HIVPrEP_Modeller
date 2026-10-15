# Phase 2 Agent Handoff — Task Board

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** This task board is retained at
> its original provenance path and contains known stale entries. It must not be
> used as the current work queue. Start at `documentation/INDEX.md`.

**Created:** 2026-09-09
**Owner:** Rishi Malhotra
**Supersedes:** §9 of `documentation/archive/historical_workstreams/phase2/PHASE2_STRATEGY_BRIEF_2026-09-06.md` (written before Phase 0 results; the sequence below is current)

This is an executable task board, not a strategy document. The reasoning behind every decision here lives in `documentation/archive/historical_workstreams/phase2/PHASE2_STRATEGY_BRIEF_2026-09-06.md`. **Read that first.** If a task below seems wrong, the brief probably explains why it is not — and if it genuinely is wrong, raise it rather than improvising.

---

## 0. Read before starting

In this order:

1. `documentation/archive/historical_workstreams/phase2/PHASE2_STRATEGY_BRIEF_2026-09-06.md` — full state, decisions, evidence, rejected paths. §12 lists paths already ruled out; do not reopen them.
2. `PHASE2_DEVELOPMENT_CHECKLIST.md` — gate IDs (R0/CAL/UNC/HTA/ENG/REL) and the Decision Log. Cross-reference IDs when reporting.
3. `outputs/calibration/target_reachability_audit_20260906/TARGET_REACHABILITY_AUDIT.md`
4. `outputs/hta_results/probe_diagnostics_20260906/PROBE_DIAGNOSTICS.md`
5. `outputs/hta_results/non_msm_decision_relevance_probe_20260906/NON_MSM_DECISION_RELEVANCE_PROBE.md` — note its headline conclusion is **not citable** (brief §15.5); its Path-B finding is.

---

## 1. Ground rules

These come from the project instructions and are not negotiable.

- **Never invent a parameter value.** No "typical values", no filling gaps with plausible numbers. If a value is needed and not available, stop and report it as a blocker.
- **Label every claim** as *unsupported*, *weakly supported*, or *well-supported*. Label anything you infer as *assumption*, *approximation*, or *derived estimate*.
- **Keep the layers separate:** model assumptions, empirical inputs, derived results, interpretation. Blurring these is a critical error.
- **Do not describe calibration output as a posterior.** Decision D-04 is settled: this is a history-matched plausible-fit ensemble. No "posterior", no "credible interval".
- **Preserve historical results.** Never overwrite an existing `outputs/` or `results/` directory. New work goes in a new timestamped directory.
- **Read files before editing.** Never re-type a file's contents from earlier tool output — it may have been truncated. Use in-place edits or read-modify-write.
- **Stop and ask** rather than proceeding through unresolved ambiguity that affects a conclusion.
- **Every task below has an acceptance criterion.** Do not mark a task done until its criterion is demonstrably met, with evidence recorded.

### Reporting format

For each task, produce a short markdown report in the task's output directory containing: what was run (exact commands), what was found (numbers, not adjectives), whether the acceptance criterion was met, evidence paths, and anything surprising. Add a row to the Completion Log in `PHASE2_DEVELOPMENT_CHECKLIST.md`.

---

---

## 1A. Verification, code review, and academic integrity

**This is academic work intended for peer-reviewed publication.** Everything below is a hard requirement, not a style preference. A task completed without meeting these is not completed — it is worse than not started, because it puts an unverified claim into a document someone will later rely on.

### 1A.1 Verification — every number must be re-runnable

- **Report only numbers you computed in this session** from an artifact that still exists. If you are repeating a figure from an earlier report, say so explicitly and say which report.
- **Record the exact command**, config path, seed, git commit and dirty state for every run you cite. A number without a reproduction path is not evidence.
- **Re-run, do not re-cite, when the code has changed.** `demography.py`, `run_v3.py`, `run_abc_smc_calibration.py` and `run_snpe_calibration.py` were all modified on 2026-09-06. Any figure produced before that date is suspect — this is precisely how the thesis numbers stopped reproducing.
- **Read the file, do not summarise from tool output.** Tool output is truncated. If you are citing a CSV or JSON value, read it from the file.
- **Verify the artifact exists and contains what you claim.** Open the output you just wrote and check it. An empty or partial file that a script reported as written has happened in this project before.
- **Distinguish sharply** between "I ran X and observed Y", "an earlier report states Y", and "I infer Y". Use those three framings literally.
- **State n.** Any variance, standard deviation, mean or interval must carry the number of replicates behind it. The existing `V_ensemble` figures are n=15 with a wide CI on the variance itself; do not present them as settled.

### 1A.2 Code review — on every change you make

- **Read the surrounding code and understand its invariants before editing.** Several bugs in this project were introduced or masked by changes made without reading the calling context.
- **Refactoring a computation requires a proof of equivalence.** For A-2 specifically: a unit test on a synthetic fixture demonstrating the new and old formulations agree exactly, before the switch is made in production code.
- **Run the full test suite before and after.** Confirm the failure set is unchanged except where you intended to change it. The ENG-01 work did this correctly — it temporarily reverted the fix to prove all 59 failures reproduced identically on the pre-fix code, and therefore that none were attributable to the change. **Match that standard.**
- **Add tests alongside the change, not afterwards.** A change without a test that would have caught the bug is incomplete.
- **Adversarially review your own change before reporting it.** After implementing, deliberately try to find the bug in what you just wrote. Two of the three bugs found on 2026-09-06 were found this way and the third was missed for a full session.
- **Never fix a symptom you do not understand.** If a number becomes correct and you cannot explain why, you have not fixed anything. Report it as unexplained.
- **Do not silently widen scope.** If a task requires changing something outside its stated boundary, stop and report rather than expanding.

### 1A.3 Academic integrity — the non-negotiables

- **Never invent a parameter value.** No "typical values", no plausible-looking placeholders, no filling a gap to keep a run moving. A missing value is a blocker to report, not a number to supply.
- **Never cite a source you have not verified.** Every empirical or methodological claim needs a real, identifiable source with traceable metadata — author, year, journal, or URL. If you cannot verify it exists and says what you claim, do not use it.
- **Do not overgeneralise a study** beyond its population, setting or intervention. When summarising, preserve the original study context.
- **Do not merge conclusions from different papers** without saying that is what you are doing.
- **Do not average conflicting estimates.** Where the literature disagrees, present the competing values and explain the methodological difference.
- **Label every inference** as *assumption*, *approximation*, or *derived estimate*. Label every claim as *unsupported*, *weakly supported*, or *well-supported*.
- **Never relabel an assumption as a finding**, or a model output as data.

### 1A.4 The tuning prohibition — read this twice

**Do not adjust any tolerance, threshold, weight, bound, discrepancy term or parameter in order to make something pass.**

The entire methodological argument for this rebuild is that tolerances are *derived from uncertainty budgets* rather than *chosen to produce acceptance*. An agent that widens `V_discrepancy` until NROY is non-empty, relaxes a gate until a config accepts, or adjusts a bound until a target becomes reachable **destroys the central claim of the paper** — and does so invisibly, because the output looks better.

If something must change for a run to pass:

1. Stop.
2. Report what would need to change and by how much.
3. Report what that change would imply scientifically.
4. Wait for a decision.

A failing gate, an empty NROY, an unreachable target and a wide uncertainty band are all **results**. They are reported at the same prominence as favourable findings. Several of the most valuable findings in this project so far have been negative ones.

### 1A.5 Self-check before marking any task complete

Answer all of these in the task report. If any answer is no, the task is not done.

- [ ] Is the acceptance criterion met, with a number rather than an assertion?
- [ ] Can every figure I reported be reproduced from a command I have written down?
- [ ] Did I read the output files I am citing, rather than trusting the script that wrote them?
- [ ] Did I run the full test suite, and is the failure set explained?
- [ ] Does every code change have a test that would catch the bug it fixes?
- [ ] Have I tried to break my own change?
- [ ] Is every source verified, and every inference labelled?
- [ ] Did I change any tolerance, threshold or bound? If so, was it derived, and did I say so?
- [ ] Am I reporting anything inconvenient less prominently than anything favourable?
- [ ] Is there anything I could not explain that I have quietly left out?

### 1A.6 Disagree when you disagree

The project instructions call for direct, unfiltered feedback, and that applies to you. If a task on this board is wrong, based on a mistaken premise, or would produce a misleading result, **say so instead of executing it.** Compliance with a bad instruction is not helpfulness here. Flag it, explain why, and propose the alternative.

---

## 2. Current state in one paragraph

The epidemiological and demographic layers are sound. The QALY-accumulation layer is not: incremental QALYs are computed as a difference of two whole-population totals, leaving the intervention effect below the model's numerical resolution (brief §15). There is **no valid frozen baseline** — the code has changed since the thesis and two independent quantities no longer reproduce. The non-MSM epidemic is effectively absent from the model; the fix is decided (exogenous observed forcing) but not implemented. The calibration is being rebuilt as Bayesian history matching, replacing ABC-SMC. The manuscript track is entirely unblocked.

---

## 3. Task board

Tasks are grouped into tracks. **Tracks A, B and C are independent — work can proceed in parallel across them.** Within a track, order matters.

Status key: `[ ]` not started · `[~]` in progress · `[x]` complete and verified · `[!]` blocked

---

### TRACK A — QALY estimator repair

**Blocks:** every economic result (ICER, threshold price, NMB, PSA, multi-fit robustness).

#### `[x] A-1 — Variance decomposition of ΔQALY by health state` — OWNER: RISHI + CODEX, COMPLETE 2026-09-10

- **Objective:** confirm or refute the mechanism hypothesised in brief §15.4.
- **Method:** decompose `total_qalys_disc` into susceptible / on-PrEP / HIV-state components and compare arms for the base case comparison.
- **Acceptance:** the per-component contribution to ΔQALY and to its across-seed variance is quantified. If the susceptible component — which should be near-identical between arms — carries most of both, the hypothesis is confirmed.
- **If refuted:** stop. Do not proceed to A-2. Report and escalate; the diagnosis is wrong and the next step is unknown.
- **Result:** acceptance met. Across 15 stochastic seeds, the susceptible/no-PrEP
  component received 95.318% of the exact covariance allocation of total ΔQALY
  variance; combined susceptible QALYs accounted for 95.297%. Total ΔQALY
  correlated 0.99369 with the between-arm person-year difference. Evidence:
  `outputs/hta_results/a1_qaly_variance_decomposition_20260910_012537/A1_QALY_VARIANCE_DECOMPOSITION.md`.
- **Encouraging secondary finding, do not lose this:** HIV-state ΔQALY correlates
  **r = -0.954** with infections averted -- the correct direction and magnitude.
  The utility model is wired correctly to the epidemiology; the fault is in
  aggregation, not in health-state valuation. Total ΔQALY correlates only
  **r = -0.418** with infections averted because susceptible person-time noise
  swamps the coherent HIV signal. Susceptible-on-PrEP SD is 21.4 versus
  susceptible-no-PrEP SD 40,179 -- the noise is specifically in the residual
  no-PrEP susceptible pool, not in PrEP-state accounting.
- **Important consequence:** the per-timestep differenced component calculation
  already reproduces the legacy whole-total subtraction within `6.3e-7` QALYs
  and retains the same 41,917.9-QALY SD. A-2's arithmetic is therefore not a
  sufficient repair for the observed stochastic variance. Do not switch the
  production comparator before a process-level/common-random-number diagnostic.

#### `[!] A-2 — Differenced accumulation` — BLOCKED PENDING REDESIGN 2026-09-10 · **NOW THE CRITICAL PATH FOR THE ESTIMATOR (A-4 review, 2026-09-10)**

> **CRN reduced the magnitude of the noise, not its nature.** A-1 found ΔQALY correlated r = 0.9937 with the between-arm person-year difference. Recomputing across A-4's 15 paired post-CRN seeds: **r = +0.9978 with Δ discounted person-years**, and only **r = +0.1221 with infections averted** (−0.1167 with Δ HIV deaths). ΔQALY is still, essentially perfectly, a person-time difference and still almost uncorrelated with the epidemiological benefit it is meant to measure.
>
> Signal-to-noise makes the same point structurally: `infections_averted` 6.14 and `delta_hiv_deaths` 5.42 — clean — against `delta_qaly` **0.80**, `delta_undiscounted_person_years` 0.40, `delta_final_population` 0.26, `delta_births` 0.04. **ΔQALY sits with the population-level demographic channels, not the HIV channels**, because it differences two whole-population totals.
>
> **So the remaining variance is structural, not stochastic-routing, and no further CRN work will reduce it.** Stated fairly on the other side: for the *mean* estimand the current estimator is already adequate (3.09 SE from zero at n=15, ~19 s per paired run), so if the manuscript reports the mean with an interval rather than claiming per-seed sign stability, A-2 is a quality improvement rather than a hard blocker.



- **Objective:** re-express the incremental QALY calculation so it is numerically well-conditioned.
- **Current code:** `scenario_comparator.py:186` — `delta_qaly = intervention.total_qalys_disc - reference.total_qalys_disc`. Accumulation is in `economics.py:450-480`, over the entire population including ~10-17M susceptibles.
- **Change:** compute `ΔQALY = Σ_t Σ_state (n_intv[t,state] − n_ref[t,state]) × u[state] × discount[t]`. Algebraically identical; per-step differences are small numbers rather than differences of large ones.
- **Constraints:** keep the existing `total_qalys_disc` outputs intact (do not remove them) so the old and new computations can be compared directly. Add the new quantity alongside, then switch the comparator to it once verified.
- **Acceptance:** (a) unit test proving algebraic equivalence on a synthetic fixture with exact arithmetic; (b) on the real base case, old and new agree to within floating-point tolerance in deterministic mode; (c) the new computation is the one `scenario_comparator` uses.
- **Depends on:** A-1 confirming the diagnosis.
- **Blocker from A-1:** the proposed per-timestep differenced arithmetic was
  evaluated diagnostically and agreed with the legacy whole-total subtraction
  within `6.3e-7` QALYs, while the stochastic SD remained 41,917.9 QALYs. The
  variance is carried by a real between-arm person-time imbalance, so this
  algebraic rewrite cannot meet the intended repair objective by itself.

#### `[ ] A-3 — Verify the fix` — CLOSURE CRITERION RESTATED BY THE A-4 REVIEW, 2026-09-10

> **The 12/15 positive-ΔQALY count carries no information and must not be the reason this stays open.** With A-4's mean 956.199 and SD 1,200.142, the effect-to-noise ratio is 0.797, so P(a single seed > 0) = Φ(0.797) = 0.787 and the **expected** number of positive seeds out of 15 is **11.81**. Observed 12. A per-seed sign test on an estimator whose residual SD exceeds its effect flips roughly one seed in five *by construction*; no amount of correct CRN changes that, only a smaller residual SD does.
>
> **Close A-3 on the mean, or defer it to A-2 — do not close it on per-seed signs.** The mean is already resolved: **956.2 ± 309.9, i.e. 3.09 SE from zero**, and 0.33 SE from the deterministic +854.568. If instead the requirement is per-seed sign stability, that is a requirement on estimator *variance* and belongs to **A-2**, not here.



- **Objective:** establish that the incremental estimator now behaves.
- **Method:** re-run the base case and `scenario_a_lena_substitution` at all switch levels, deterministic and across 15 stochastic seeds (reuse `outputs/hta_results/probe_diagnostics_20260906/raw/seed_variability_batch.py`).
- **Acceptance — all three must hold. Criterion 3 added 2026-09-10, because A-1 showed the base case alone is not sufficient:**
  1. Deterministic ΔQALY sits within ~1 standard error of the stochastic mean. (Pre-fix reference: base case det 854.6 vs stoch mean 25,358.7 ± 10,824 — a ~2.3 SE gap. Scenario C was sign-flipped: det +1,252.1 vs stoch mean −21,678.7 ± 7,769.)
  2. ΔQALY agrees in sign with `infections_averted` for the base case comparison in every seed. (Pre-fix: `infections_averted` positive 15/15, ΔQALY negative 4/15.)
  3. **`scenario_a_lena_substitution` behaves coherently at every switch level in both modes**, or its behaviour is fully explained by named components per A-7. The base case comparison passing is **not** sufficient — it was already coherent deterministically before any fix was applied.
- **Record:** the new across-seed SD, against the pre-fix 26,700–45,000.
- **Interim A-4 evidence (2026-09-10):** base-case criterion 1 passes: deterministic ΔQALY +854.6 is 0.33 SE from the CRN stochastic mean +956.2 (SE 309.9). The SD is 1,200.1. Criterion 2 does **not** pass literally: infections averted are positive 15/15, while ΔQALY is positive 12/15. Criterion 3 has not been rerun stochastically under CRN, and the deterministic switch_100 person-time channel remains subject to A-9. A-3 therefore stays open.

#### `[x] A-12 — Audit every deterministic allocation site for the cellwise-rounding pattern` — COMPLETE 2026-09-10

A-11 proved that cellwise `round(n_cell x p)` in `demography.py` gave the intervention arm a spurious background-mortality advantage, purely because putting people on PrEP fragments the same population across more tensor cells and more fractional deaths get rounded away. ENG-01 fixed **two functions in one file**. A-11 confirmed that was the whole story *for this comparison*. It did not establish that no other site has the same pattern.

- **Objective:** find every deterministic allocation site in the model and classify each as aggregate-preserving or cellwise.
- **Where to look:** transmission/infection allocation, disease progression, HIV and background mortality, ART initiation and dropout, diagnosis, PrEP uptake/dropout/waning, migration entry and exit, aging, births. Grep for `np.round`, `round(`, `astype(int)`, `floor`, `ceil`, and any per-cell multiplication by a rate followed by an integer cast.
- **The test for each site:** does the number of events depend on how the same population is distributed across cells? If yes, it carries the same differential bias between arms and the magnitude scales with how many people the intervention moves between states.
- **Quantify, do not assume.** For each cellwise site found, measure the **differential** effect between arms — not the aggregate effect on totals. See the materiality note below.
- **Acceptance:** a complete inventory of allocation sites with a classification for each; for every cellwise site, a quantified between-arm differential on the base-case comparison; and a recommendation on which to convert to `stochastic_allocation.py`'s aggregate-preserving allocator.
- **Why this outranks A-4:** if another site carries the same bias, CRN would partially mask it in stochastic mode while leaving the deterministic base case wrong. Find them first.

**Materiality must be assessed on the estimand, not the aggregate — the lesson from ENG-01.** ENG-01's own materiality check recorded "+1,032 background deaths of 7,225,644, +0.014%, small in aggregate" and proceeded. That check ran on **totals**. The defect was **differential between arms**, and on the incremental estimand it was the dominant term in the entire analysis — a 4x error in base-case ΔQALY. For a CEA the estimand is always incremental. Never again clear a rounding or allocation change on an aggregate percentage.

**A-12 result (2026-09-10): acceptance met.** Report: `outputs/hta_results/a12_deterministic_allocation_audit_20260910_103000/A12_DETERMINISTIC_ALLOCATION_AUDIT.md`. Codex reviewed 258 broad rounding/cast syntax candidates across the seven population/event modules and resolved them into 51 distinct allocation contracts. Ten are genuinely fragmentation-sensitive, but **all ten contribute 0 people to the base-case between-arm differential**: they are shared before the fork, configured to zero, disabled, dormant/unreachable, or part of inactive fallback/legacy/AE modes. The current recurrent paths for transmission, progression, diagnosis, ART, PrEP, mortality, migration, births, and production residence-queue aging are aggregate-preserving, fixed-total, exact transfers, or correctly stratified by substantively different rates. This clears A-4 for the **current base-case path**; it is not permission to enable a fallback or legacy path without repair.

One non-incremental defect remains visible: initial age-sex truncation allocates 13,598,889 people from a configured 13,598,912 (**−23**). It cancels between arms because both restore the same frozen 2025 snapshot, but should be converted to exact fixed-total allocation before the eventual production/calibration baseline is frozen. Also found and quarantined: an unreachable deterministic MSM role-split transmission branch, the inactive constant child-mortality fallback, unsafe disabled population scaling, three legacy aging/debut paths, and state-machine AE severity resolution that drops residual resolved cases. The report gives a repair/remove recommendation for every site. **No production behavior or parameter changed.** Focused verification: 173 passed / 39 warnings; full-suite failure/error set unchanged from A-11 (58 failed / 1,640 passed / 2 skipped / 90 warnings / 5 errors in 202.54s after adding five A-12 tests).

**A-12 independently reviewed 2026-09-10 (Claude, Cowork). Verdict: the audit holds; acceptance status "met" is correct.** Addendum, originals untouched: `outputs/hta_results/a12_deterministic_allocation_audit_20260910_103000/ADDENDUM_2026-09-10_independent_review.md`.

Re-derived from source rather than trusted: the `INIT-01` mechanism and the consistency of its 23-person magnitude; all four dormancy config facts read directly from the base config; the correctness of `deterministic_allocate_events`; and `migration._apply_negative`, which contains the literal ENG-01 pattern (`np.floor(pop_float).astype(np.int64)` across the whole tensor) and is **correctly classified** — floor plus a largest-remainder add-back that restores exactly `n_remove`. That was the most likely place for a missed second ENG-01 and it is clean. **The audit runner also reproduced the delivered headline exactly, to full double precision, on Linux / Python 3.10.12 / numpy 2.2.6 against Codex's macOS / Python 3.11.13 run** — stronger reproducibility evidence than the report claims for itself, and worth citing in the manuscript.

**One defect found and fixed:** `migration._apply_negative`'s largest-remainder add-back used `np.argsort(-combined)`, which is quicksort and **not stable**, unlike the explicit `kind="stable"` in `stochastic_allocation.deterministic_allocate_events`. Remainders there are heavily tied (16,000 of 20,000 cells at exactly 0.0 in a realistic fixture), so the two sort kinds select genuinely **different cells while adding the identical total** — invisible to a totals check, which is the ENG-01 lesson restated. The path is live: net migration is negative in 2004–2007, inside the baseline history that produces the frozen snapshot. Fixed to `kind="stable"`; **measured bit-neutral** on this platform (all eleven incremental metrics and both arm totals byte-identical before and after), so it cannot disturb A-4 criterion (d).

**Two gaps closed, both demonstrated rather than asserted:**
- *The regression guard was function-level; the defect class is call-level.* `syntax_candidates()` raises on an untriaged `(module, function)`, but **54 audited functions hold 2–12 rounding calls each** (`run_v3.record_outputs` 18, `migration.apply` 12, `run_v3._allocate_fixed_events` 10), so a new cellwise round inside a triaged function inherits its disposition silently — the shape in which ENG-01 was introduced. Proof: injecting `np.round(population * 0.5)` into `migration._apply_negative` leaves A-12's own five tests **passing**; the new call-site guard **fails and names it**. Fixed additively — `INVENTORY`, `NON_ALLOCATION_FUNCTIONS` and `syntax_candidates()` are untouched; `tools/a12_call_fingerprints.json` freezes 242 distinct call sites / 258 calls, and `diff_call_sites()` reports additions and removals.
- *Dormancy was argued in a CSV, never asserted in a test.* Four of A-12's five tests are inventory-schema tests. Flip `aging_method`, enable `population_adjustments`, switch `adverse_events.mode`, or reintroduce a caller for `initialize_childhood`, and the ten "0 people" rows silently become false with every test still green. `tests/test_a12_review_guards.py` adds **16 tests** naming the site, mechanism and recommended repair in each failure message.

**Two corrections to the delivered text** (recorded in the addendum, originals untouched): (1) `TX-02`'s row says the truncating branch "is reached only by the stochastic caller" — it is not; both truncations sit behind `if deterministic:` and the stochastic caller passes `False`, so the branch is dead in **both** modes, which is the stronger claim. (2) The headline covers "the deterministic `base_case_context` evaluation path" but the scan covered seven modules; `run_scenarios.py` (the fork), `state_snapshot.py` (the frozen state) and `economics.py` (the estimand) are on that path and were not scanned. All three verified clean — `economics.py` has **zero** array-valued rounding, `run_scenarios.py` has two year labels, and `_collapse_prep_to_none` is an exact `sum(axis=-1)`. The claim survives; it should say the boundary was checked. Also noted: `adverse_events.mode` lives in the **HTA** config, not the model config, so `AE-03`'s dormancy depends on a different file from the other four config-gated sites.

**Not changed:** any file delivered with A-12, or A-12's own test file. `tests/test_a12_deterministic_allocation_audit.py` still passes 5/5.

#### `[x] A-4 — Common random numbers across arms` — COMPLETE 2026-09-10

A-1 located the variance; this is the repair. No longer optional and no longer conditional on wanting a stochastic PSA.

- **The decisive evidence A-1 produced.** Within a *single* seed, the two arms' total discounted QALYs diverge by roughly 50,000 (seed 42: +52,099; seed 43: −41,091) against an intervention effect of ~855, on arm totals of ~473.45M. Both arms fork from the *same* frozen 2025 state with the *same* seed. **A within-seed between-arm divergence of that size cannot be sampling variation — it can only come from the arms consuming different random draws.** Corroborating: ΔQALY correlates r = 0.9937 with `delta_person_years`, whose between-arm difference is mean +37,804 / SD 60,738 stochastically versus only +511.8 deterministically.
- **Why deterministic mode looks better here, stated fairly:** with no draws at all, deterministic mode is *implicitly perfectly synchronised* between arms, so its person-year difference reflects only the intervention's real mortality effect. That is a genuine point in favour of the current convention and it was under-weighted in the 2026-09-06 review.
- **Recommended implementation — counter-based RNG, not sequential substreams.** A sequential stream desynchronises the moment the two arms consume a different number of draws, and every draw downstream shifts. Instead key the generator on `(seed, year, timestep, process_id, stratum_id)` so the *same* uniform is used for the *same* process in the *same* stratum at the *same* time in both arms, regardless of draw history. `numpy` ships `np.random.Philox` for exactly this. The residual between-arm difference then reflects only the count difference the intervention actually caused.
- **Expected limit, so nobody chases perfection:** draws act on aggregate counts, so exact cancellation is impossible — a binomial on a different population count differs even given the same uniform. That is correct behaviour. The goal is removing divergence caused by *different random numbers*, not divergence caused by *different populations*.
- **Acceptance — all five. Criterion (e) added 2026-09-10:** (a) within-seed between-arm QALY divergence falls to the order of the intervention effect rather than ~50,000; (b) across-seed SD of ΔQALY falls materially from 41,917.9; (c) the null-perturbation determinism check still passes; (d) **deterministic results are bit-unchanged** — CRN must not alter the deterministic path; (e) **judge success on the A-11 person-time diagnostic, not on ΔQALY SD alone** — report between-arm Δ background deaths, Δ HIV deaths, final population, and undiscounted/discounted person-years. A-11 showed person-time is the channel; ΔQALY is downstream of it.

- **Criterion (d) now has a verified reference to diff against (A-12 review, 2026-09-10).** Reproduce `base_case_context` with `tools/run_a12_deterministic_allocation_audit.py` (~12 s) and diff `base_case_incremental_summary.json` against these eleven values, which held **exactly, to full double precision, across two platforms and interpreters** (macOS-26.5.1-arm64 / Python 3.11.13 project venv, and Linux-6.8-aarch64 / Python 3.10.12 / numpy 2.2.6):

  | metric | value |
  |---|---:|
  | `delta_qaly` | 854.5676382184029 |
  | `infections_averted` | 827 |
  | `delta_discounted_person_years` | 387.2243775129318 |
  | `delta_undiscounted_person_years` | 511.75 |
  | `delta_background_deaths` | 36 |
  | `delta_hiv_deaths` | −41 |
  | `delta_childhood_deaths` | 0 |
  | `delta_births` | 1 |
  | `delta_sexual_debuts` | −8 |
  | `delta_net_migration` | 0 |
  | `delta_final_population` | 6 |

  `base_case_arm_summary.csv` and `base_case_path_facts.json` are byte-identical across those two runs and are unaffected by the 2026-09-10 `kind="stable"` migration fix, which was measured bit-neutral. **A non-zero diff on any of these means CRN has altered the deterministic path and criterion (d) has failed.**

- **REQUIREMENT carried from the A-12 review: every ordering CRN introduces must be explicitly deterministic AND explicitly stable.** `migration._apply_negative` used `np.argsort(-combined)` — quicksort, not stable — for its largest-remainder add-back. Because the remainders there are heavily tied, the unstable sort added the **same total to different cells**: invisible to any totals check, and precisely the ENG-01 failure mode. CRN introduces new orderings by construction — stratum iteration order, counter composition, residual assignment, any tie-break in allocating draws to cells. Each one must use `kind="stable"` or an explicit total order, and must be judged on **which cells** receive events, not only on how many. `stochastic_allocation.deterministic_allocate_events` is the pattern to follow.
- **Depends on:** A-12 (find any other cellwise-rounding sites first — CRN would mask them in stochastic mode while leaving the deterministic base case wrong).
- **Depends on:** A-7 — **discharged, and it did exactly what the dependency anticipated.** A-7 is complete and it surfaced the second, independent defect: **A-9**, a deterministic substitution timing/allocation defect that is explicitly "independent of stochastic RNG desynchronisation", so CRN will not fix it.
- **Consequence for criterion (e), noted 2026-09-10 — read before choosing what to measure.** A-9 makes intervention-arm adult person-time fall below oral PrEP at switch_100, with infections averted non-monotonic across the grid (+38, +7, +34, +113, +96). Criterion (e) says to judge CRN on the person-time diagnostic — **which is the very channel A-9 is known to corrupt in the substitution scenarios.** So: measure criterion (e) on `base_case_context` (oral PrEP vs no PrEP), where A-7 confirmed deterministic mode is coherent (ΔQALY +854.6 with +827 infections averted). **Do not read CRN success or failure off the substitution arms until A-9 is resolved** — a person-time signal there mixes the CRN effect with a defect CRN cannot touch. A-9 does not block A-4 starting; it blocks interpreting A-4 on the switch grid.

**A-4 result (2026-09-10): all five acceptance criteria met.** Report: `outputs/hta_results/a4_common_random_numbers_20260910_170048/A4_COMMON_RANDOM_NUMBERS.md`. The stochastic path now uses Philox streams keyed by seed, year, absolute timestep, named process and explicit stratum. Probability draws use one keyed uniform per shared coordinate and a discrete inverse CDF; adding a PrEP tensor slice cannot shift the shared slices. Key serialization is an ordered primitive tuple with a fixed regression fingerprint, and the implementation never uses Python's randomised `hash()` or an unstable sort.

**CRN upgrade-compatibility guard added 2026-09-10.** `tests/test_a13_crn_key_compatibility.py` now pins four separate golden boundaries: the exact pickle-protocol-5 payload, its 32-byte BLAKE2b digest, the production little-endian `uint32` key words, and the first eight float64 uniforms emitted by NumPy Philox at that key/counter. A Python or NumPy upgrade that shifts the stream therefore fails at the boundary that moved instead of silently changing every stochastic draw. These vectors must not be mechanically refreshed: a change requires an explicit compatibility decision and rerunning all stochastic evidence.

- Across the same 15 seeds (42–56), mean base-case ΔQALY was **+956.2**, SD **1,200.1**, versus the pre-CRN SD **41,917.9**: a **97.14% reduction**. The within-seed range was **−1,630.3 to +2,797.4**, not approximately ±50,000. Mean infections averted was **836.5** (SD 136.4), positive in all 15 seeds.
- Criterion (e), assessed only on `base_case_context` as pre-specified: mean Δ background deaths **+39.7**, Δ HIV deaths **−100.0**, Δ final population **+53.3**, Δ undiscounted person-years **+725.0**, and Δ discounted person-years **+468.4**. Raw per-seed values and ranges are retained in the report.
- The deterministic eleven-metric A-12 reference reproduced **exactly**, including ΔQALY `854.5676382184029`; the full 20-arm null perturbation hash check passed, with only the deliberately changed Scenario C arm differing.
- The stochastic mean lies **0.33 SE** from the deterministic estimate, but only **12/15** stochastic ΔQALYs are positive despite 15/15 positive infections averted. That is a major improvement, not permission to mark A-3 complete: its stricter sign-coherence question remains open and should be revisited with A-5b's larger economic run.
- Performance was treated as part of production readiness: an initial correct inverse-CDF implementation took about 90 seconds per paired run under contention. Reusing a Philox engine at explicit counters and calling the lower-level exact discrete quantile reduced the retained run to a mean **19.0 seconds per paired seed** under four-worker contention. Focused verification: **85 passed**; full suite: **58 failed / 1,681 passed / 2 skipped / 90 warnings / 5 errors**, the same pre-existing 58-failure/5-error set with 25 new passing A-4/A-5a tests.

#### `[x] A-7 — Decompose the decision-relevant comparison` — OWNER: RISHI + CODEX, COMPLETE 2026-09-10

**A-1 examined only `base_case_context` (oral PrEP vs no PrEP), where deterministic mode is already coherent: ΔQALY +854.6 with +827 infections averted.** The sign incoherence that started this investigation appears in a *different* comparison, in *deterministic* mode, where the desynchronisation mechanism cannot explain it:

| deterministic, `scenario_a_lena_substitution` | ΔQALY | infections_averted |
|---|---|---|
| switch_10 | **−36.9** | **+38** |
| switch_25 | −312.6 | +7 |
| switch_50 | −646.1 | +34 |

- **Objective:** run the A-1 decomposition on `scenario_a_lena_substitution` at all five switch levels, deterministic, and establish whether negative ΔQALY alongside positive infections averted has a legitimate mechanism or is a second, distinct defect.
- **PrEP-state disutility is RULED OUT — verified 2026-09-10, do not re-investigate.** In `configs/hta_costing_config.json`, `utilities/susceptible`, `utilities/susceptible_oral_prep` and `utilities/susceptible_lenacapavir` are **all exactly 1.0**, each sourced to Nichols et al. 2016 (Lancet Infect Dis 16:1423, Table S1) and NICE TA1106 (2025), both of which apply zero PrEP decrement. The config notes record that a previous unsourced 0.005 lenacapavir differential was deliberately removed. There is also no separate waning utility, so the waning state inherits 1.0. **Moving people between PrEP states therefore changes nothing in QALY terms.**
- **Consequence — the sharpened hypothesis.** With every susceptible utility at 1.0, deterministic ΔQALY reduces to (between-arm difference in person-time alive x 1.0) minus HIV-state utility shortfalls minus decrements. Since HIV utilities are below 1, averting infections must *raise* QALYs. So a negative deterministic ΔQALY alongside positive infections averted implies **the intervention arm has fewer person-years alive** — which preventing infections cannot cause. In deterministic mode there are no random draws, so desynchronisation cannot explain it either.
- **Therefore check first:** the sign and magnitude of `delta_person_years` for `scenario_a_lena_substitution` at each switch level, deterministic. If `delta_person_years` is negative while `infections_averted` is positive, this is a **deterministic accounting/allocation defect — the same family as ENG-01 (cellwise rounding dropping rare events), now between arms — and A-4's common random numbers will NOT fix it**, because no random numbers are involved. That would mean two distinct defects with two distinct fixes: stochastic desynchronisation (A-4) and deterministic allocation (new task).
- **Remaining mechanisms still worth checking, in order:** `delta_utility_decrement_adverse_events` (lenacapavir adverse-event profile differing from oral); HIV-state *timing* shifts moving person-time into lower-utility stages even with fewer total infections; and whether `infections_averted` is accumulated over a different window than the QALY accumulation.
- **Why this outranks A-1 in importance:** this is the comparison the paper's conclusion rests on. The base case comparison is context, not the decision. A-1's acceptance was met on a comparison that was never the problem case.
- **Acceptance:** the sign of deterministic ΔQALY at each switch level is fully explained by named components, and it is stated whether that explanation is a legitimate model mechanism or a defect. **If a defect, do not fix it in this task — report it.**
- **Result:** acceptance met. At switch 10/25/50/75/100, deterministic
  ΔQALY was −36.929/−312.632/−646.081/−547.768/−1,827.409 while
  infections averted was +38/+7/+34/+113/+96. The exact identity
  `ΔQALY = Δ discounted adult person-years + Δ HIV utility shortfall +
  Δ utility decrements` reconciled within `5.1e-7` QALYs at every level.
  Lenacapavir adverse-event decrements were
  −174.848/−440.436/−893.126/−1,365.569/−1,840.645 QALYs and flipped the
  sign at every level; excluding only that component, ΔQALY was
  +137.919/+127.805/+247.045/+817.801/+13.236. This is an **intentional
  model mechanism, not an unexplained accounting residual**, but is only
  **weakly supported**: the utility losses are cabotegravir proxies and the
  lenacapavir prevalence/duration mapping is approximate and decision-critical.
  Evidence:
  `outputs/hta_results/a7_decision_comparison_decomposition_20260910_141500/A7_DECISION_COMPARISON_DECOMPOSITION.md`.
- **Secondary defect found and not fixed:** switch_100 had +96 infections
  averted but −33.5 undiscounted adult person-years (−32.047 discounted),
  meeting A-7's pre-specified defect condition. Population mass-flow accounting
  still reconciled exactly and final total population was +8, so this is a
  timing/allocation defect rather than missing mass. It is small relative to the
  −1,840.645-QALY adverse-event component and does not explain the overall sign.
  Track separately in A-9; A-4 will not repair a deterministic path.

#### `[x] A-10 — Lenacapavir adverse-event disutility: the sign-determining parameter` — COMPLETE 2026-09-10

**A-7 established that the entire negative ΔQALY is produced by one weakly-evidenced parameter block.** Removing the lenacapavir AE decrement flips ΔQALY positive at *every* switch level:

| Switch | ΔQALY (as configured) | Lenacapavir AE decrement | **ΔQALY excluding AE** |
|---:|---:|---:|---:|
| 10% | −36.9 | −174.8 | **+137.9** |
| 25% | −312.6 | −440.4 | **+127.8** |
| 50% | −646.1 | −893.1 | **+247.0** |
| 75% | −547.8 | −1,365.6 | **+817.8** |
| 100% | −1,827.4 | −1,840.6 | **+13.2** |

The mechanism is `0.405 × 0.01 + 0.355 × 0.02 = 0.01115` QALY lost per discounted lenacapavir user-year, reproduced from the config to within `4.55e-13`.

**The asymmetry, verified in `configs/hta_costing_config.json` 2026-09-10:**

- `utilities/susceptible`, `utilities/susceptible_oral_prep`, `utilities/susceptible_lenacapavir` are all exactly **1.0**.
- `adverse_events/_oral_prep_ae_exclusion_note` records that `oral_prep_initiation_renal_screen`, `oral_prep_gi_ae` and `oral_prep_renal_ae` were **removed from the base case on 2026-05-22** on iPrEx/PROUD/IPERGAY evidence.
- The two lenacapavir AE items were **retained**, with `utility_decrement.distribution_status = sourced_proxy` on both, sourced to NICE TA1106 EAG 05 **cabotegravir** disutility, and Beta(1,99)/Beta(2,98) priors whose own notes read *"Wide uninformative prior pending literature sourcing."*

So oral PrEP's adverse-event disutility was removed as unsupported while lenacapavir's was kept on a proxy — and that single asymmetry determines the sign of the paper's primary outcome.

**State the two sides fairly; do not present this as an error:**

- **Well-supported:** that lenacapavir carries a real, drug-specific injection-site burden. EMA CHMP Yeytuo (EMA/265754/2025) gives PURPOSE-1 68.8% and PURPOSE-2 83.2% any-ISR, with ~35–36% non-resolving nodules at end of observation. Oral PrEP has no comparable persistent local reaction, so an asymmetric *frequency* is evidence-driven, not arbitrary.
- **Weakly supported:** the *utility weight* (−0.01 / −0.02), transferred from cabotegravir with no lenacapavir EQ-5D data, and the prevalence-to-duration mapping, which the EPAR itself flags as having unclear nodule duration.

**Tasks:**

1. Confirm no oral-PrEP AE disutility is active anywhere in the base case, and that the 2026-05-22 removal rationale is fully recorded with its iPrEx/PROUD/IPERGAY citations.
2. Compute the **ICER and threshold price with the lenacapavir AE decrement set to zero**, at every switch level. (At switch_10: Δcost €415,317,952 / ΔQALY +137.9 ≈ **€3.01M/QALY**, roughly 38x the €80,000 threshold — a *derived estimate*, verify it.)
3. Compute a **symmetric-treatment variant**: either both arms carry AE disutility, or neither does.
4. Report a one-way sensitivity across the full plausible range of the two utility weights, including zero, and identify the value at which the dominance verdict flips.
5. **Check whether the AE decrement explains any of the thesis non-reproduction.** Preliminary arithmetic suggests it gives a finite ICER but does *not* recover the thesis's €9,987/yr threshold price, so it is at most part of that story — confirm and report what remains unexplained.

**Acceptance:** all four variants (as-configured, no-lenacapavir-AE, symmetric, and the one-way sweep) reported side by side, with the dominance-flip point identified and the evidence status of every input labelled.

**Reporting implication — feeds F-2 and F-8:** "Dominated" is a fragile characterisation resting on a proxy parameter, and it is the single most attackable claim currently in the paper. **"€3.0M/QALY, ~38x the Dutch threshold, even with no adverse-event penalty applied at all"** is both robust and stronger, because it is conservative against the paper's own conclusion. Recommend making the no-AE result co-primary. **Do not change the base case unilaterally — this is Rishi's decision (new D-09).**

**Result (2026-09-10):** Acceptance met. Direct deterministic zero-AE reruns
gave positive ΔQALY at every switch level but ICERs of EUR 3.011M, 8.271M,
8.671M, 3.993M and 332.440M/QALY, with NMB=0 annual threshold prices of EUR
2,629, 1,536, 1,484, 1,951 and 1,103. Switch 10 is 37.64 times the EUR 80,000
threshold even with no AE penalty. The permitted symmetric “neither arm”
variant is identical to zero Lena AE because no oral-PrEP AE disutility is
active; adding injection disutility to oral users would invent an unsupported
input. Exact joint AE-weight multipliers at which ΔQALY becomes negative are
0.789, 0.290, 0.277, 0.599 and 0.007 across switch 10–100. The configured
Beta distributions have mathematical support [0,1], but no empirical
lenacapavir utility-weight bounds exist; the full-support sweep is explicitly a
stress test, not an empirical plausible interval. Removing AEs does not recover
the earlier EUR 9,987/year thesis threshold price (current: EUR 2,629 at switch
10), so AEs explain the sign but not the larger non-reproduction. The oral-PrEP
exclusion note now contains full iPrEx/PROUD/IPERGAY citations and correctly
labels zero incremental QALY/cost as a weakly supported structural assumption.
No base-case value changed; D-09 remains open. Evidence:
`outputs/hta_results/a10_lena_ae_sensitivity_20260910_025004/A10_LENA_AE_SENSITIVITY.md`.

#### `[x] A-11 — Localise the thesis QALY gap` — COMPLETE 2026-09-10

**Correction to A-10's conclusion:** A-10 stated the thesis residual "cannot be assigned causally because no valid frozen thesis baseline exists." **The thesis-era outputs do exist on disk** at `results/thesis_submission/hta_split_v13_22_nearfit_rank001/` (`comparison_table.csv`, `price_sensitivity.csv`, `arm_summary.csv`, per-scenario `summary_table_*.csv` and `timestep_*.csv`), and the source config path is recorded in `results/working_sub/V13.22_20260604_nearfit_rank001_basecase_PSAReady.json` as `calibration_metadata.abc_smc_calibration.source_config_path` = `Modeller/v11/V13.22_20260604_final_candidate_rank012.json`. This is a tractable diff, not an unresolvable residual.

**What the thesis file actually contains (read 2026-09-10):**

| | thesis `switch_10` | current `switch_10` (no AE) |
|---|---:|---:|
| delta_cost | €424,166,104 | €415,317,952 |
| delta_qaly | **+1,224.47** | +137.92 |
| ICER | €346,408/QALY | €3,011,315/QALY |
| **infections_averted** | **−56** | **+38** |
| dominance | trade-off | trade-off |

**The thesis's published headline ICER rests on ΔQALY = +1,224 accompanied by infections_averted = −56.** More infections *and* more QALYs is not a coherent pair. The current model's sign pattern is coherent; the thesis model's was not. This is a defect in the submitted analysis, not in the current code, and it needs to be understood before any comparison between the two is used.

**Where the gap lives — it is not the AE block.** Health consequence per averted infection, base-case comparison:

| | thesis | current | ratio |
|---|---:|---:|---:|
| ΔQALY per averted infection | 4,489.6 / 1,070 = **4.20** | 854.6 / 827 = **1.03** | 4.1x |
| Δ person-years per averted infection | 5,732.25 / 1,070 = **5.36** | 511.8 / 827 = **0.62** | 8.6x |

Infection counts are broadly similar between versions; what changed is the **health consequence attributed to each averted infection**. That points at health-state utilities, mortality/progression parameters, or person-time accumulation — not at adverse events, and not at the transmission model.

**Which is right is genuinely open.** For HIV in the Netherlands with near-normal ART life expectancy, a plausible range is roughly 2–5 discounted QALYs per averted infection (*derived estimate — verify against the model's own mortality and utility parameters*). The thesis figure of 4.20 sits inside that; the current figure of 1.03 sits below it. **Do not assume the current model is the correct one.** This connects directly to A-8, which should be merged into this task.

**Tasks — run task 0 first; it is decisive and it is one run.**

0. **Bisect config-change against code-change.** Three cells, two of which already exist:

   | config | code | result |
   |---|---|---|
   | thesis-era | thesis-era | on disk: `results/thesis_submission/hta_split_v13_22_nearfit_rank001/comparison_table.csv` |
   | **thesis-era** | **current** | **run this** |
   | current | current | have it (A-7 / A-10) |

   Run the thesis-era config (`Modeller/v11/V13.22_20260604_final_candidate_rank012.json`, path recorded in `results/working_sub/V13.22_20260604_nearfit_rank001_basecase_PSAReady.json` under `calibration_metadata.abc_smc_calibration.source_config_path`) through the **current** code, deterministic, same scenarios.
   - If it reproduces **thesis-like** QALYs per averted infection (~4.2), the change is in the **configuration** — proceed to the parameter diff below.
   - If it reproduces **current-like** values (~1.0), the change is in the **code** — the config diff will not find it, and the search moves to `economics.py`, `demography.py`, `run_v3.py` and `stochastic_allocation.py` between the thesis commit and now.
   - If neither, both changed and the contributions must be separated.
   **Do this before any parameter diffing.** It halves the search space in a single run.

1. Diff `Modeller/v11/V13.22_20260604_final_candidate_rank012.json` against `configs/V13.22_20260604_nearfit_rank001_basecase.json` and `configs/hta_costing_config.json`, focusing on health-state utilities, mortality and progression parameters, discounting, and horizon. Report every difference that could move QALYs per averted infection.
2. Check specifically whether the thesis-era config carried the **+0.005 lenacapavir utility advantage** that the current config's notes record as removed for being unsourced (`utilities/susceptible_lenacapavir`). Quantify its contribution — preliminary arithmetic suggests roughly +78 QALYs at switch_10 against a 1,086-QALY gap, so it is a contributor, not the explanation.
3. **Reconcile the base-case comparison before touching `switch_10`.** The base case is sign-coherent in both versions (thesis: +4,489.6 QALYs / +1,070 infections; current: +854.6 / +827), so the 4.1x per-infection gap shows there cleanly, without the confound of the thesis's negative infections_averted. Fix the general case first. If the 4.1x per-infection gap reproduces in the base case, the cause is general, not scenario-specific.
4. Establish the mechanism behind the thesis's **negative** infections_averted at `switch_10`, and whether it also affects the thesis's reported `scenario_c` and add-on results.
5. Conclude which version's QALYs-per-averted-infection is defensible, with the model's own mortality and utility parameters as the reference.

**Acceptance:** the 4–9x gap is attributed to named parameters or code paths with quantified contributions; a reasoned conclusion is stated on which version is correct; and the thesis's negative-infections-averted result is explained.

**Escalate immediately if** the conclusion is that the submitted thesis materially overstated or understated the intervention effect — that affects what can be said about the submitted work, and is Rishi's call.

**Result (2026-09-10): acceptance met; escalation triggered.** The raw rank012
file named in `source_config_path` is not the executed rank001 parameter set: it
differs from the PSAReady rank001 config at 36 model-value leaves. The PSAReady
rank001 and current rank001 configs have zero model-value differences. Using the
actual rank001 values, committed code reproduces the historical base case and
`switch_10` exactly (base: +4,489.637 QALYs / 1,070 infections averted;
`switch_10`: +1,224.471 QALYs / −56 infections averted), while current code gives
+854.568 / 827 and −36.929 / +38. Bidirectionally swapping only `demography.py`
swaps the complete result exactly. The cause is the former cellwise rounding of
deterministic background deaths: PrEP/HIV state fragmentation changed how many
fractional deaths were discarded. Aggregate-preserving mortality allocation
reduces base-case discounted person-time gain from 3,822.823 to 387.224 QALYs and
the total ratio from 4.196 to 1.033 QALYs per averted infection. The thesis value
is therefore not defensible despite lying inside the rough 2–5 heuristic; the
current value is internally coherent with the model's high ART utility/survival
assumptions but is not externally validated. The same artifact reverses the
infections-averted sign in thesis `switch_10`, `switch_25`, `switch_50`, and
add-on 500. No production behavior or parameter was changed. Evidence:
`outputs/hta_results/a11_causal_flow_20260910_044500/A11_QALY_GAP_LOCALISATION.md`.
Rishi must decide how to describe the materially overstated submitted result.

#### `[x] A-8 — Person-years-per-averted-infection plausibility check` — **SUPERSEDED by the revised brief and COMPLETE 2026-09-14; see "RESULTS + NEW FINDINGS 2026-09-14". The framing below (42.9 vs 0.62) is pre-CRN and obsolete.**

- **The arithmetic:** stochastic mean `delta_person_years` +37,804 over mean 882 infections averted = **~42.9 discounted person-years gained per averted HIV infection**. Deterministic: +511.8 over 827 = **~0.62**.
- In the Netherlands, where ART gives near-normal life expectancy, ~43 discounted person-years per averted infection is not mechanistically plausible; ~0.6 is low but not obviously wrong under 4% discounting across a 40-year horizon.
- **The open question:** with SD 60,738 at n=15, SE ≈ 15,682, so the stochastic mean sits ~2.4 SE from the deterministic value. **That is not enough to distinguish "large variance around the deterministic value" from "variance plus a genuine upward bias."** If it is pure variance, A-4 fixes it. If there is bias, A-4 will not, and that must be known before A-4 is judged successful.
- **Method:** derive the expected discounted life-years gained per averted infection independently, from the model's own mortality and progression parameters, and compare against both observed figures. Re-check after A-5's larger n.
- **Acceptance:** an independently derived expected value, labelled a *derived estimate*, compared against both figures, with a stated conclusion on whether bias is present.

#### `[x] A-13 — Deterministic mode averts 41 HIV deaths; stochastic mode averts 100` — DIAGNOSTIC COMPLETE 2026-09-10

- **Finding.** Comparing A-4's deterministic guard against its 15-seed stochastic mean, **every one of the eleven metrics agrees within 1.6 SE except `delta_hiv_deaths`, which disagrees by 12.4 SE** (deterministic −41 vs stochastic −100.00, SE 4.76). The next-largest discrepancy is smaller by a factor of eight. This is an isolated, systematic disagreement, not scatter: the stochastic estimate has signal-to-noise 5.4 and every seed lies in [−135, −74], so no seed comes near −41.
- **Why it matters.** The production convention is **deterministic**. Mortality is the dominant route by which averted infections become QALYs, so if deterministic mode compresses the between-arm mortality difference by a factor of 2.4, the base-case ΔQALY is built on a mortality signal that much smaller — and the manuscript's central estimate inherits it. **ΔQALY's own agreement (0.33 SE) is uninformative here**: its SE is 310, far too coarse to detect a discrepancy of this size.
- **Not necessarily a bug.** Deterministic mode substitutes rounded expectations for binomial draws, and for a nonlinear cascade E[f(X)] ≠ f(E[X]); a multi-stage progression (infection → chronic → AIDS → death) is exactly where that gap grows. **It is unexplained, which is the point.** Per §1A.2, never fix a symptom you do not understand — diagnose first.
- **Relationship to A-9.** Close kin and possibly a shared mechanism, but **A-9 is scoped to the substitution arms and this appears in `base_case_context`**, which A-9 explicitly does not cover. Run them together if the mechanism looks common.
- **Method:** trace between-arm HIV-mortality flow from the common frozen state in both modes; identify the first year and process where the deterministic difference falls below the stochastic mean. Diagnostic only — change no production behaviour.
- **Acceptance:** the mechanism is named with an accounting trace, and a judgement is recorded on whether the deterministic base case understates mortality-mediated QALY gain. If it does, that is a manuscript disclosure item, not only a code fix.
- **Evidence:** `outputs/hta_results/a4_common_random_numbers_20260910_170048/ADDENDUM_2026-09-10_review_A4_A5a.md` §3.

**A-13 result.** The mechanism is **cross-stage residual assignment by `deterministic_allocate_events()`**, upstream of mortality. Diagnosis, ART-initiation and ART-dropout calls span Chronic and AIDS cells. The allocator preserves one rounded aggregate call total but not stage-specific expectations; stable C-order largest-remainder assignment systematically shifts hundreds of events from AIDS into Chronic while aggregate checks remain exact. In the deterministic no-PrEP/oral arms, AIDS diagnoses are 666.8/903.4 below expectation and AIDS ART starts are 695.3/604.3 below expectation. The first annual endpoint more than 2 stochastic SE below the mean is 2026 at AIDS-stage ART initiation (−1 deterministic versus +0.40 stochastic mean, SE 0.19).

**Review addendum (Claude, 2026-09-10) — independently reproduced, plus the generalisable lesson.**

*Reproduced in isolation:* one `deterministic_allocate_events` call, one scalar `p`, two strata (600 large-count cells + 600 small-count cells). The rare stratum is under-allocated by **9.2%** while the aggregate is preserved to within one event; allocating **per stratum** instead is exact to 0.1 events. Mechanism and fix-shape both confirmed. Confirmed in source that `diagnosis`, `art_initiation` and `art_dropout` each pass one whole tensor with a single scalar `p` (`run_v3.py` 1609 / 1666 / 1682).

*Why it does not cancel:* the two arms are distorted by **different amounts** (AIDS diagnoses −667 no-PrEP vs −903 oral-PrEP), because the residual ranking depends on arm state. A distortion that cancelled would be harmless; this one is larger in the intervention arm, so it suppresses apparent benefit.

**This retroactively amends A-12 and its independent review.** Both examined `deterministic_allocate_events` and passed it — A-12 classified it `aggregate_preserving` with "Keep"; the review verified it "correct, including the residual/capacity edge case". Both were right about what they tested. **A-12's equivalence class was "cells with the same event probability or rate"** — and these cells genuinely *do* share `p`, so by that rule they are one class and splitting them is safe. **The correct equivalence class is "same event probability AND same downstream consequence."** An AIDS-stage diagnosis and a Chronic-stage diagnosis share a diagnosis rate but lead to very different mortality, so they are not exchangeable. A-12's taxonomy had `rate_stratified_aggregate` for substantively different *rate* blocks; it had no category for substantively different *consequence* blocks. Full note: the POSTSCRIPT in `outputs/hta_results/a12_deterministic_allocation_audit_20260910_103000/ADDENDUM_2026-09-10_independent_review.md`.

**ENG-01 and A-13 are the two ends of one dial — this is the lesson that prevents a third instance.**

| | granularity | failure | visibility |
|---|---|---|---|
| **ENG-01** | too fine — cellwise `np.round(n*p)` | rare events **erased** | totals visibly wrong |
| **A-13** | too coarse — one aggregate across outcome-relevant strata | rare-stratum events **relocated** to the common stratum | totals exactly right, so it hides better |

Same failure, opposite signs, and **the fix for the first produced the second.** So **allocation granularity is a modelling decision, not an implementation detail.** Any repair must state which strata are treated as exchangeable and justify it; "HIV stage" is not automatically the answer — ART status, age and orientation are candidates wherever they carry different downstream consequences — and over-stratifying brings ENG-01 straight back.

**Recommended guard, to be built with the A-2/A-9 repair rather than now:** for every production call to `deterministic_allocate_events`, assert that the allocation preserves the expected total *within each HIV stage* to within one event. That test fails on the current code and would have caught this before A-4.

**One consequence for A-4's criterion (d).** The eleven deterministic reference values are now known to sit on a biased mortality channel. They remain a valid **reproducibility** anchor — they correctly detect whether a change perturbed the deterministic path — but they are **not a correctness anchor**, and should be described that way wherever they are cited.

The resulting off-ART AIDS exposure explains 89.2% of the mortality gap: deterministic expected off-ART AIDS deaths averted are −3.24 versus stochastic +51.67. The final mortality draw is not responsible — deterministic unrounded mortality inputs imply 36.82 deaths averted and its integer result is 41, while stochastic expectation 98.36 tracks realised 100.00. **Judgement: the deterministic base case understates mortality-mediated health gain and this is a manuscript disclosure item.** Diagnostic only; no production behavior was changed. Canonical evidence: `outputs/hta_results/a13_hiv_mortality_trace_20260910_230000/A13_HIV_MORTALITY_TRACE.md`.

#### `[ ] A-9 — Isolate deterministic substitution timing/allocation defect`

> **⚠ THIS ENTRY IS STALE (flagged 2026-09-14 during A-28).** A-9 switch-grid work **has been run** — 2026-09-13, `outputs/hta_results/a9_switch_grid_process_decomposition_20260913_231232/A9_SWITCH_GRID_PROCESS_DECOMPOSITION.md`, per `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A9_SWITCH_GRID_2026-09-13.md`. The `[ ]` and the title below describe the **original** A-7-triggered framing and were never updated. **A-28 additionally requires that run to be repeated** on the coupled samplers (see A-28's re-run list). Reconcile this entry before using the board for planning.

- **Trigger:** A-7 found switch_100 produces +96 infections averted but −33.5
  adult person-years, and infections averted are non-monotonic across the switch
  grid (+38, +7, +34, +113, +96). This is independent of stochastic RNG
  desynchronisation and is not the main explanation for negative ΔQALY, which
  A-7 traced to adverse-event decrements.
- **Objective:** identify the first timestep and event/allocation process that
  makes intervention-arm adult person-time fall below oral PrEP at switch_100,
  and determine whether the non-monotonic grid response is caused by deterministic
  integer allocation or a legitimate nonlinear epidemic mechanism.
- **Method:** trace arm differences in transmission, HIV mortality, background
  mortality, births/debut and adult-population stock from the common frozen
  state; use non-production fractional/controlled diagnostic variants only if
  required. Do not tune parameters and do not change production behavior in the
  diagnostic.
- **Acceptance:** the first divergence and responsible named process are
  identified, with an exact accounting trace and a separate proposed fix if it
  is defective. Do not implement that fix within A-9 without a reviewed design.

#### `[~] A-5 — Re-run seed variability at n = 40-50` — **A-5a COMPLETE 2026-09-11, A-5b still blocked**

- **Objective:** tighten `V_ensemble` for Track B and confirm A-3 with a narrower CI on the variance.
- **Note:** n=15 gave a wide CI on the variance estimate itself. Andrianakis convention for `V_ensemble` is the mean or 95th percentile of sample variances — record both.
- **Acceptance:** `V_ensemble` per scenario and per metric, recorded with n and the convention used.
- **Depends on:** A-3, A-4 — **but only for the economic half. Split this task (noted 2026-09-10).**
  - **A-5a — epidemiological series, for Track B: COMPLETE 2026-09-10.** Seed variability of the *calibration target series* — `PLHIV`, `new_diagnoses`, `new_diagnoses_msm`, `aids_deaths`, `on_art_stock`, `new_infections`, `new_aids_cases` — per year, n = 40–50. This is what history matching needs and it touches neither the QALY estimator nor between-arm CRN. Evidence: `outputs/calibration/a5a_epidemiological_seed_variability_20260910_162000/A5A_EPIDEMIOLOGICAL_SEED_VARIABILITY.md`. Fifty stochastic runs (seeds 7001–7050) produced **11,100 raw replicate rows** and **222 year-specific unbiased sample variances**, including every one of the 92 active tier-1 target-years; none was zero. The operative input for history matching is `raw/yearly_v_ensemble.csv`. Use the **year-specific** variance. The reported mean and p95 across target years are descriptive only: a strict Andrianakis mean/p95 across input design points is not identifiable from one fixed configuration. This run was intentionally completed on post-ENG-01, pre-A-4 code while A-4 ran in parallel; that is valid for the pre-specified single-arm marginal variance task, whose dependency explicitly excluded CRN.
  - **A-5b — economic outcomes.** ΔQALY, cost, infections averted. Genuinely depends on A-3 and A-4, because CRN changes the stochastic variance it is measuring.
  - **Note on the existing artefact:** `outputs/hta_results/probe_diagnostics_20260906/raw/seed_variability_summary.csv` supplies neither half usably — it holds economic outcomes only (no epidemiological series at all) at n=15 on pre-fix code.

#### `[ ] A-6 — Burn-in stochasticity check`

- **Design rationale for the burn-in — do not treat it as an oversight.** The 1975 start exists deliberately: to align epidemic onset with the first HIV cases historically identified in the Netherlands, and to absorb uncertainty in disease progression and initial seeding that cannot be pinned down directly. It is a designed uncertainty sink, not an unconstrained tail nobody thought about. This rationale belongs in the manuscript (see F-5) — the honest framing is "35 scored years plus a burn-in whose purpose is stated", not either "50 years of validated history" or "15 years nobody checked".
- **Objective:** deterministic mode is least defensible where numbers are smallest. 1975-1985 has very small infected counts (`seed_prevalence` ~7e-4), where real epidemics can die out or take off in ways a deterministic trajectory cannot represent. The question is how much that weakness propagates.
- **Method:** compare the deterministic burn-in trajectory against the mean of stochastic replicates over 1975-1990.
- **Relevant prior finding:** the model shows strong compounding sensitivity over long horizons — a 0.9% change in the beta multiplier produced an ~8x PLHIV difference by 2017 (brief §15.7). Whatever the burn-in starts with is amplified, so quantifying this matters more than the unscored years alone would suggest.
- **Acceptance:** the divergence is quantified and either shown immaterial or documented as a limitation, with the design rationale above stated alongside it. **This is a bounded diagnostic, not a licence to change the base-case mode.**

#### Standing decision on run mode — do not change without escalating

**Deterministic remains the base case.** Reasons: (1) the model is compartmental, where a deterministic base case is the field norm; (2) history matching puts simulator stochasticity into `V_ensemble` rather than averaging it out, so calibration waves run deterministically by design; (3) the case for switching rests on the 27k-45k SD, which A-1..A-3 may show is an estimator artifact.

Stochastic replicates are used **only** to quantify Monte Carlo error and supply `V_ensemble`. Revisit the base-case mode after A-3 and A-5, not before.

---

#### `[x] A-17 — The residual placement rule` — COMPLETE 2026-09-12. **HYPOTHESIS REFUTED.**

> **Residual placement is NOT the mechanism behind B-8's +17–30% level inflation.** `outputs/calibration/a17_residual_placement_20260912_173211/`. Refuted three independent ways: **wrong direction** (largest-remainder ends −0.1277% *below* the exact fractional trajectory, the gap is *above*); **wrong magnitude** (−0.13% against +17–30%, ~150× too small); and **the unbiased fix made it worse** (counter-based stochastic rounding, verified unbiased at +0.00093% over 500 seeds, moved the median |log ratio| 0.18838 → 0.28402 against a pre-specified threshold of <0.09419, with range coverage falling 179/222 → 73/222).
> **Kept from A-17, still live for A-13:** largest-remainder *does* misplace severely in rare-event regimes — at p=1e-4 the bottom seven size-deciles received **zero** events while deciles 8–10 were over-allocated +57.5/+17.6/+2.8%. But the general "placement error correlates with cell size" claim is **FALSE** (28 positive / 19 negative across 47 regimes) because `fractional(n·p)` is saw-toothed, not monotone. Use the size-decile view, not the Pearson r.
> **Also established:** stochastic rounding is genuinely unbiased and does **not** reintroduce ENG-01 (10,000 cells at expectation 1e-4: allocated 0.9942 vs expected 1.0; cellwise round-to-nearest allocated 0), and is bit-reproducible at fixed seed.
> **The most valuable output is the timing fingerprint** (`raw/step1_yearly_log_ratio.csv`): the PLHIV log ratio rises **+0.0062 per quarterly step, almost linearly**, 1975 (+0.004) → 1990 (+0.312 peak), then plateaus and slowly declines. That is the signature of a **constant per-step multiplicative bias accumulating through exponential growth and ceasing at saturation** — not an allocation artifact.

#### `[x] A-19 — Sainte-Laguë, D'Hondt and controlled rounding` — COMPLETE 2026-09-12

> `outputs/calibration/a19_divisor_controlled_rounding_20260912_183159/`. 12 s synthetic screen, **zero model runs**. Validity gate passed first: reproduced all four of A-17's published figures to <4e-8.
> **The divisor family is closed by PROOF.** In the rare-event regime Sainte-Laguë and D'Hondt allocated the small stratum **0 against an expectation of 6**, identical to largest remainder. Every such rule is *size-monotone*, so if any 0.01-cell won a unit all 600 of the 0.99-cells must have too — ≥601 > 600. **Closes Sainte-Laguë, D'Hondt, Huntington-Hill and Adams at once; do not test the remaining two.** *(Precision point verified in review: divisor methods are globally size-monotone so the proof covers them unconditionally; largest remainder is size-monotone only in the sub-unit regime, which is exactly the rare-event one — the conclusion is unaffected but a manuscript theorem needs the qualifier.)*
> **D'Hondt was the worst rule in the screen** (+6.106% drift, 48× the incumbent, 4× worse than the ENG-01 control).
> **Controlled rounding is the only deterministic rule that fixes Defect 1**, and only under a derived condition: `guaranteed small-stratum events = max(0, T − Σ ceil(pure-large-stratum margins))`, which predicted 6/0/0 across three layouts before the LP was solved and observed 6/0/0. **It protects a stratum iff the margin structure leaves it at least one whole event — so margin design is the modelling decision and must be declared.**
> **Defect 1 is worse than previously understood.** Over 300 steps the row holding the ten smallest cells ends at **1.8% of its exact total** — those cells never win a residual and stay frozen for the whole run. A-13 is *progressive erasure of rare strata*, not one-step misplacement.
> **The unifying result: size-monotonicity is the culprit, so you must give up either determinism or size-monotonicity.** Stochastic rounding (abandon determinism) is unbiased at every granularity with no margin design; controlled rounding (abandon monotonicity) is randomness-free but costs 1.6× drift, 61× runtime, and protects only at declared-margin granularity. **If seeded-reproducible mode is acceptable, stochastic rounding dominates.**
> **None of this touches Defect 2** (B-8's level inflation) — that remains A-18.
> **Criterion-design lesson:** A-19's pre-committed C1 could not certify its own winner (6 protected rows do not nest in 5 deciles; stochastic rounding's 11.2% is 2.59 SE of noise against a threshold below the estimate's resolution). Reported without adjusting any verdict — correct handling. **Second occurrence of a pre-registered criterion lacking the resolution to identify the right answer** (first: A-16's CV<0.50). Future briefs should state criterion resolution alongside the threshold.

#### `[x] A-18 — The Jensen hypothesis, and light probes of the three remedies` — COMPLETE 2026-09-12. **HYPOTHESIS REFUTED, TWICE OVER.**

> `outputs/calibration/a18_jensen_mechanism_20260912_205053/` (Step 1) and
> `outputs/calibration/a18_remedy_probes_20260912_211631/` (remedy probes; the earlier `…_210713/` is
> **superseded** and carries a `SUPERSEDED.md` marker). Independent review:
> `…_205053/ADDENDUM_2026-09-12_independent_review.md` — **verdict upheld.**
> **Refuted by inspection.** The model never computes `f(n) = 1 − (1−β)^n`. `transmission_corrected.py`
> runs a two-step binomial composing to `Binomial(n, prevalence·p_trans)`, expectation `n·prevalence·p_trans`
> — affine in `n` in the uncapped interior. The hypothesised functional form is **not in the model**.
> **Refuted by measurement.** The literal hybrid (contacts drawn, all else deterministic, n=50) moved the
> median |log ratio| over 222 scored target-years from **0.188377 to 0.188279** against a pre-specified
> threshold of **<0.09419** — **0.104%** of the required reduction, and **0.0047×** the median per-year SE.
> The +0.0062/step PLHIV signature is untouched (slope 0.00607 before and after). Knob control: 1,987/1,987
> traced contacts became integers and all were displaced, yet the largest disagreement anywhere was
> **5.98 people** against a **+20.7%** gap.
> **Review correction (text only, finding unaffected):** §1's claim that the second derivative is
> "identically zero in every stratum" is **overstated** — `transmission_corrected.py:518`, `:536–541` and
> `:586` apply `min()` caps, which are concave and would reintroduce a Jensen term **if they bound**.
> Measured on A-18's own trace they never bind: **0/1,995 cells**, tightest at **0.16% of capacity**.
> Carry the qualified form into the manuscript; it survives the objection instead of inviting it.
> **Probe verdicts.** A — conditional on **D-11**, which flips it (see the decision log). B — not worth
> building; NROY stays **0/4,000** and a usable NROY would need ~10× the measured discrepancy (reported,
> **not adopted**). C — **blocked**: the model carries no contact-count `σ²` to correct with, and the
> bound using its own rounding variance is **1.3e-9 infections**, bit-identical to production.
> **Three refutations now stand:** A-17 (residual placement), A-19 (the deterministic apportionment family,
> by proof), A-18 (transmission nonlinearity in the contact count). **Defect 2's mechanism is unidentified
> and A-18 correctly declines to propose a fourth.** Next: **A-20**, below.

#### `[x] A-18 — original specification, retained for provenance` — SPEC WRITTEN 2026-09-12

**Brief: `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_JENSEN_MECHANISM_2026-09-12.md`.** Diagnostic plus three cheap remedy probes; implements nothing.

**The hypothesis (unverified).** Per-person infection probability given `n` contacts is `f(n) = 1 − (1−β)^n`, which is **concave** (`f'' = −(1−β)^n·[ln(1−β)]² < 0`). Deterministic mode computes `f(E[n])`; the stochastic model computes `E[f(n)]`. By Jensen, **`f(E[n]) ≥ E[f(n)]` — deterministic systematically overstates transmission.** Matches B-8's signature **six for six**: positive sign, HIV-compartment-specific, linear per-step accumulation through growth, plateau at saturation, immunity to every allocation change tested, and the right magnitude (~0.6%/step over ~60 steps ⇒ ~1.45× against an observed peak of 1.37×). **A hypothesis that explains everything is the kind most in need of a falsification attempt** — hence Step 1.

**Terminology that matters.** Jensen's inequality is a **mathematical fact**, not a modelling choice. The *modelling choice* was running the simulator **deterministically** — substituting expected values for draws. That choice is written down and **reversible**, which is why the remedies are genuine options rather than mitigations.

**Step 1 is decisive and comes first:** a hybrid run with **contact counts drawn stochastically and everything else deterministic**, plus a direct `f(E[n]) − E[f(n)]` arithmetic check from the retained traces. Pre-specified criterion: median |log ratio| falls by at least half (0.18838 → <0.09419), matching A-17 so the two are comparable. **If it fails, stop** — that would be the third proposed mechanism and the second refutation, and guessing a fourth is not the answer.

**Three light remedy probes:** (A) score the existing deterministic and A-5a stochastic trajectories through the *same* implausibility calculation — no new runs — and report **both** directions, since averaging n seeds tightens the denominator by √n even as it removes the inflation; (B) carry the measured gap as a derived discrepancy term and rescore the existing 4,000 particles — **mechanism-agnostic, so worth running even if Step 1 fails**, and the one probe exempt from the stop rule; (C) an opt-in second-order `½·f''(μ)·σ²` correction to the deterministic force of infection, one run. **(C) is the most attractive outcome if it works** — it keeps deterministic speed and exact reproducibility while removing the bias — and therefore the one most at risk of being adopted on insufficient evidence.

**§1A.4 boundary on probe B, stated explicitly in the brief:** the discrepancy term must be **derived from the already-measured gap, written down before rescoring, and never adjusted afterwards**. If NROY is still 0, that is the answer.

**If Jensen is confirmed, the inflation is not a bug** — it is the known cost of a declared approximation, and therefore a disclosure item and a design decision rather than an error.

---

#### `[x] A-20 — Process-by-process decomposition of Defect 2` — COMPLETE 2026-09-12. **THE GAP LOCALISES TO BACKGROUND MORTALITY.**

> `outputs/calibration/a20_process_decomposition_20260912_221520/` (Step 1),
> `…/a20_step2_localisation_20260912_230327/` (Step 2),
> `…/a20_process_decomposition_analysis_20260912_231933/` (consolidated tables).
> Independent review: `…_221520/ADDENDUM_2026-09-13_independent_review.md` — **localisation upheld, interpretation challenged.**
> **The result.** Twelve arms, n=50, seeds 7001–7050. **Background mortality is the only material arm**: median |log ratio| **0.18838 → 0.04901**, displacement **+0.13937 = 6.76 median SE**, **74.0%** of the baseline gap recovered, median level move **15.93%**. Every other arm is a null recovery or an adverse shift. Three large adverse overshoots: HIV mortality (**+122% vs stochastic, −29.65 SE**), disease progression (−10.85 SE), transmission (A-18, reused, −7.07 SE). Both equivalence corners exact; B-8 reproduced 222/222; diagnosis–ART interaction measured (median 0.0028) and no arms summed.
> **The brief's own starting condition was FALSIFIED and A-20 says so.** The winner opens in **1975**, not 1995. The A-18 addendum's "opens when diagnosis and ART come online" prior was labelled weak evidence and is now discharged — diagnosis ranks 4th at +0.15 SE and ART is *adverse* at −1.28 SE.
> **Design worth reusing.** The switch-surface inventory found **31** physical branch sites by AST against the brief's static estimate of 33, discovers sites at runtime and **raises on an unmapped branch** rather than pinning a stale count; shared dispatchers (`_draw_events_for`, `_process_rng`) have ownership resolved by runtime `process_id` rather than assigned to one arm. The gate caught real drift (13→12 demography, 14→13 transmission).
> **REVIEW CORRECTION — the one inference that does not follow.** A-20 concludes the winner is "too large for allocation alone," citing **A-17's ~0.13%**. A-17 measured *residual placement* — which cell gets a leftover — applied **globally**; A-20's arms remove allocation entirely, which is **A-19's** quantity, and A-19 measured the smallest-cell row ending at **1.8% of its exact total**. There is no ≤10% ceiling on compositional relocation, so **the ≥10% band is still not separable from allocation removal.** This changes what the win means, not the ranking.
> **LEADING MECHANISM CANDIDATE — hypothesis, tested by A-21.** `demography.py:_calculate_life_table_mortality` (lines **981/989**, live, 204 calls/run each) passes `population[age_idx, orient_idx, :, :, :, :, :]` to `deterministic_allocate_events` at **one shared rate** — a block mixing the huge HIV-negative cells with every tiny HIV-positive cell. The allocator **preserves the block aggregate** and awards residuals by fractional part; HIV+ cells sit in the sub-unit regime and lose essentially every contest, so their background deaths are **relocated to the HIV-negative cells**. That is **Defect 1 with a site**, not a fourth mechanism, and it predicts B-8's signature including the one fact nothing else explains: **+14.6% HIV stock against −0.004% total population.**
> **It also re-reads A-17's null.** The allocation effect has **opposite signs across processes** (background mortality +0.139 vs progression −0.224, HIV mortality −0.611, transmission −0.146), so a global rule swap nets out negative. A-17's result was never evidence against allocation — it was evidence that a **global fix is the wrong instrument**, and only a per-process decomposition could show that.
> **UNVERIFIED AT THE A-20 REVIEW; RESOLVED BY A-21 BELOW.** The Jensen hypothesis also matched six observations before A-18 refuted it. A-21's direct trace subsequently confirmed this candidate.

**Original specification retained below for provenance.**


**Brief: `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_PROCESS_DECOMPOSITION_2026-09-12.md`.** Measurement only; implements nothing. Ends in a localisation, or in an explicit statement that the gap does not localise.

> **Read §4 before designing the harness.** The mode switch is **not** a single chokepoint. `_draw_events_for` (`run_v3.py:493`) covers progression, diagnosis, ART, PrEP and HIV mortality, but `run_v3.py:1205` branches directly (and rounds with `np.round`, not `deterministic_allocate_events`), and `demography.py` (13 sites), `migration.py` and `transmission_corrected.py` receive the flag as a **call argument**. An arm patched only at the chokepoint would silently null out ageing, births, migration and background mortality. The brief makes a **switch-surface inventory a mandatory gate** for exactly this reason.

**Why this and not a fourth hypothesis.** Three mechanisms have been proposed and three refuted. A-18 §6
and the review addendum §4.3 both conclude that the right response is to **measure which process carries
the bias** rather than guess another functional form. This task is a measurement, not a hypothesis.

**The method.** Hold every process deterministic except one; draw that one stochastically; compare against
both the fully deterministic and the fully stochastic reference at n=50, seeds 7001–7050. Nine processes:
transmission (already done — it is A-18's `hybrid_transmission` arm), progression, diagnosis, ART
initiation, HIV mortality, background mortality, births, migration, ageing. The A-18 harness
(`tools/a18_jensen_mechanism.py`) already demonstrates the parameterised-copy-plus-equivalence-control
pattern and should be reused rather than rewritten.

**Two starting conditions established by the A-18 review, and they cut the work substantially.**

1. **Start the comparison window at 1995, not 1975.** `hybrid_transmission` and the fully stochastic
   reference agree to within ~1% through 1990 (+0.2% / +4.5% / −1.0% at 1980/1985/1990) and then separate
   monotonically: −12.8% (1995), −19.7% (2000), −23.8% (2005), −27.0% (2010), **−28.5% (2015)**, −25.7%
   (2025). Since the only difference between those two arms is that the non-transmission processes are
   deterministic in one, the divergence that opens after 1995 is located in **the non-transmission
   processes**, in the era when diagnosis and ART come online.
2. **Instrument diagnosis and ART first.** They are the processes that change behaviour at that date.

**Three cautions that are binding, not decorative.** (a) The channels are **not additive** — this is a
nonlinear dynamical system and no share of the gap may be attributed by subtraction. The result above is a
statement about *where two curves separate*, which is an observation, not a decomposition. (b) It is
**one parameter point** at n=50. (c) It **narrows rather than eliminates**, so it is weaker evidence than
any of the three refutations and must be labelled as such.

**A second candidate that costs nothing to check first.** A-13 already localised a deterministic defect to
**cross-stage residual assignment in the diagnosis/ART calls** — the same two processes §4.2 points at, on
a different symptom. Check whether A-13's mechanism, scaled up, produces a level effect of the right sign
and size before building the full nine-arm sweep.

**Equivalence control is mandatory,** on the A-18 pattern: any parameterised copy must reproduce
production deterministic **and** production stochastic annual outputs bit-for-bit over 1975–2025 before
any hybrid result is read, and the runner must **raise** rather than report if either corner fails.

**Criterion resolution must be stated alongside any threshold** (third occurrence of this lesson: A-16's
CV<0.50, A-19's C1, and A-18's own — which passed, at 4.57× the median per-year SE of 0.020609).

**This does not block the manuscript.** Under D-14 Rec 1 the base case moves to the stochastic mean, which
does not contain Defect 2. A-20 determines whether the **calibration** can be run deterministically, and
whether the inflation is a disclosure item or a repairable defect.

---

#### `RESULTS + NEW FINDINGS 2026-09-14` — read this before picking up any A-track task

**Completed 2026-09-14 (reports are self-contained; figures below are pointers, not substitutes):**

| task | status | one-line result | report |
|---|---|---|---|
| **A-23** | `[x]` | The +122% is the **transmission feedback**: holding FOI at the deterministic trajectory collapses it (M_held/M_free = 0.011; residual +21.3% vs stochastic ref = deterministic mode's own +20.7% offset). Trigger (measured, not isolated): deterministic HIV mortality over-allocates deaths to MSM, starves non-MSM (24.5% of expected). **Brief's cross-stage premise was wrong at this call site** — the allocator is called per stage × ART. Conservation identity closes exactly. A-13 vs A-20 sign "contradiction" resolved: between-arm difference vs level. | `outputs/calibration/a23_hiv_mortality_anomaly_20260913_235530/A23_HIV_MORTALITY_ANOMALY.md` |
| **A-2** | `[x]` | Decomposition reconciles (227 controls, max residual 4.6e-7). Infection-coupled components +1,592.7 mean but **2.4% of Var(ΔQALY)**; non-HIV life-years (debuts 61.4%, background deaths 36.2%) carry 97.6% of variance with mean −636.5. Partial r(ΔQALY, IA \| ΔPY) = 0.988; LY via HIV deaths r_IA = +0.70 → infection channel present, **r = +0.12 is swamping, not absence**. A-16's 183 = switch-10 near-zero denominator artefact. Births desynchronise between arms under CRN (unexplained → A-24). | `outputs/hta_results/a2_estimator_decomposition_20260913_235517/A2_ESTIMATOR_DECOMPOSITION.md` |
| **A-8 (revised)** | `[x]` | **1.911 discounted QALYs per averted infection is what the model's parameters imply** (cohort derivation 1.83–1.94, agreement criterion ±0.116). Low because horizon-truncated (lifetime 4.01; mean averted-infection year 2044.6). Harmonised published composites 2.29–2.93 vs model-comparable 1.60 (max 2.76×, below 3× escalation). **Judgement: disclosure + sensitivity, not a defect.** No direct NL per-infection comparator exists. | `outputs/hta_results/a8_qaly_per_infection_20260914_005530/A8_QALY_PER_INFECTION.md`; brief `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A8_QALY_PER_INFECTION_2026-09-14.md` (supersedes the A-8 entry below) |
| **A-24** | `[x]` (one escalation open) | **CRN keys are 100% bitwise aligned** (9.77M base / 11.55M switch-100 paired draws, incl. transmission and HIV mortality; `unscoped_event_draw` never reached in stochastic mode) → A-2's option B as framed removes nothing. **Branch C.** Births drift = **migration allocation sampler**: `migration.py:1043` uses `Generator.multinomial` (not inverse-CDF) weighted by current adult age × sex counts, so tiny between-arm differences flip the allocation of ~14,000 migrants/quarter (sampler share of Var(Δbirths) 1.70 [1.09, 2.17], offset by ageing redistribution −0.85; female fertile-age HIV deaths differ by exactly 0 → why births don't track IA). Background-death drift = **cell-movement re-randomisation** (per-cell keys; people moved between PrEP slices re-drawn). Harness-only counterfactual coupling **both** samplers: Var(ΔQALY) ratio **0.090 [0.028, 0.265]**, SD 1,200 → 360 (≈11× seeds); no resolved mean shift (max 1.31 SE) → **variance, not bias**. A-9's stochastic-only recommendation stands for means; **seed-level switch-grid analyses not safe** (switch 100: LY via debuts 0.59 share of Var(ΔQALY)). **Open escalation:** cannot rule out drift changing seed-level monotonicity of A-9 grids (per-seed IA shift SD ≤80 vs mean steps 27–33); follow-up run specified in report (~1.2 h). | `outputs/hta_results/a24_entry_pathway_crn_drift_20260914_010031/A24_ENTRY_PATHWAY_CRN_DRIFT.md` |

**Decisions now open for Rishi (do not decide unilaterally):**
- **A-3 criteria 1–2:** A-2 proposes closing on a restated criterion (mean 3.09 SE from 0; infection-coupled component positive and resolved; both sub-channels coupled to IA; sign flips attributable to non-HIV variance). Must be recorded as an explicit restatement, not a silent weakening.
- **A-3 criterion 3:** "coherent in both modes" is unattainable as written — A-9 found no coherent deterministic repair, while the stochastic grid is coherent (min increment 4.56 SE). Restate or scope to stochastic, **after A-24** confirms the stochastic grid is unaffected by the CRN drift.
- **Estimand (A-2 §8):** keep population ΔQALY + decomposition (recommended); HIV-attributable co-primary overstates by up to 1.67×; cohort estimand not recommended. **Option B is superseded by A-24:** choose between (i) **more seeds** — n≈61 halves SE, n≈167 matches both-sampler coupling; ~19 s/paired seed; changes no existing result; does nothing for per-seed sign/monotonicity — or (ii) **couple both samplers** (migration multinomial + grouped background mortality; touches `counter_based_rng.py` and/or `migration.py` / background-mortality code, with tests) — every stochastic result changes, so A-4, A-5a, A-16, A-2, A-9 and A-15 stochastic references need re-running; Philox/key golden vectors unaffected. A-24's suggestion: seeds if only the headline mean matters; coupling if per-seed or switch-grid QALY evidence is needed.
- **A-24 open escalation — follow-up DROPPED 2026-09-14, superseded by A-28.** The seed-level monotonicity question (does the drift change per-seed monotonicity of A-9's grids?) is answered directly by the A-9 re-run that A-28 already requires, on the coupled production code. Partial follow-up kept: `run_cf_both_s10/` (15 seeds, 0 errors; usable as a harness-vs-production reproduction control at switch 10 after A-28). `run_cf_both_s25` was stopped incomplete and should be deleted. To support the follow-up, `a24_harness.py` `SCENARIOS` gained `s10/s25/s50/s75` (commented in the file; earlier runs unaffected). **Runtime note:** 10 workers on this 14-core / 24 GB machine thrashed (20.5 GB swap, 739 s/seed vs 106 s/seed at 4 workers) — use ≤4 workers for instrumented A-24-harness runs.
- **Thesis disclosure (A-8):** report horizon-truncated per-infection value beside the lifetime value; sensitivity inputs listed in A-8 §7 (MSM diagnosis rate 0.24/yr → 2.63, 0.06/yr → 5.13; on-ART utility 0.95/0.89/0.85 → 2.09/2.60/2.94; on-ART survival Trickey all-comers → 2.61).

**New findings from A-8 — found out of scope, NOT changed, each needs its own task:**

##### `[ ] A-25 — PrEP eligibility age cap silently ignored` — CONFIRMED BY CODE INSPECTION 2026-09-14
- `configs/V13.22_20260604_nearfit_rank001_basecase.json` lines 1508 and 1576: `eligible_age_range.max = "64-69"`. **No age bin has that name** — the bin is `"65-69"`.
- `run_v3.py` ~1829 (uptake) and ~2789 (MSM coverage denominator) resolve it with `self.groups.age_to_idx.get(name, n_age_groups - 1)`, so an unknown name **silently falls back to the last bin**. Eligibility therefore spans **15 to 110+** instead of 15–69. A-8 confirmed programmatically (`params_used.json` → `oral_prep_eligible_max_bin_resolved: "110+"`).
- **Scope of impact:** every PrEP scenario (uptake, coverage denominator, who receives PrEP, where infections are averted). **The typo is in every config in `configs/`**, including `v6_golden_reference.json`, `v11_configuration.json`, `V13.18_20260601_basecase.json`, `V13.22_20260604_final_candidate_rank012.json`, `v13_14_1975_burnin_base.json`, `v13_24_1970_population_snpe_base.json`, `model_final.json`, `1980_test.json` (grep `'"max": "64-69"'`, 2026-09-14). **It therefore predates the submitted thesis and affects its results too** — a disclosure question for Rishi, like A-11.
- **Two defects, not one:** the typo, and the **silent fallback** — an unknown bin name should raise, consistent with ENG-F04's "KeyError preferred over silent wrong-cohort".
- **Fixing it changes production results** and invalidates every PrEP-scenario run to date (A-2, A-4, A-9, A-16, A-15 …). Needs a measured before/after at fixed seeds before any decision. Rishi's call.

##### `[ ] A-26 — Infection risk has no age dimension`
- `transmission_corrected.py` 339–370: contacts are split within orientation × activity in proportion to susceptible count; **age plays no role**. All base-case averted infections are MSM (no MSM↔heterosexual route, lines 117–121).
- **Consequence:** averted infections follow the age structure of susceptible MSM — including 70+ — rather than the young-adult concentration seen in Dutch surveillance. Combined with A-25, averted infections are **old-skewed**. Older infections carry fewer remaining life-years (A-8: 4.81 QALYs at 25–29 vs 3.88 at 60–64 for a 2025 infection).
- This is a **structural modelling choice**, not a bug. Decision: disclose, or add age-assortative mixing / age-specific risk (a calibration-level change). Check whether the calibration targets constrain age at diagnosis at all.

##### `[ ] A-27 — HIV life-years lost fall with age; evidence says they rise`
- A-8 `vansighem2010_check.txt`: someone diagnosed off ART loses **4.6** undiscounted life-years at age 25 in the model vs **0.4** in van Sighem et al. 2010 (AIDS 24:1527–35, ATHENA); at 55, **1.0** vs **1.4**.
- **Mechanism (A-8 §6):** model HIV mortality is **additive and age-independent**, so a constant excess hazard accumulates over more remaining years at young ages. Driven mainly by lifetime chronic→AIDS progression on ART (0.004/yr) and AIDS mortality on ART (≈0.027/yr). With on-ART HIV mortality set to 0, loss at 25 is 0.84. A multiplicative structure (SHM mortality ratio 1.9) gives horizon-truncated net loss 2.04 vs the model's 1.60.
- **Caveat:** van Sighem is ART-naive at 24 weeks, 1998–2007, asymptomatic at diagnosis, heavily extrapolated — the 0.4 level is optimistic. **The direction of the gradient is the finding, not the exact level.**
- **Possible offsetting errors (unverified inference):** the model gives young infections too large a loss (A-27) but averts too few young infections (A-25, A-26). 1.911 may be approximately right *in level* partly because these cancel. Any fix to one without the others could move the headline per-infection value materially.
- Related config flag: the chronic→AIDS on-ART rate's `source` field reads `"NEED DIST BLOCK????? Mocroft A et al. (2010) …"` (e.g. `configs/V13.22_20260604_final_candidate_rank012.json:1296`) — an unfinished citation on an A-8 sensitivity input (range 0.001–0.015 → 1.77–2.03).

##### `[x] A-28 — Couple the two samplers that break CRN pairing` — **COMPLETE 2026-09-14. THE FIRST IMPLEMENTATION TASK SINCE A-12.**

**Brief: `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A28_CRN_COUPLING_FIX_2026-09-14.md`.** Independent review of the whole batch: `REVIEW_ADDENDUM_BATCH_2026-09-14.md`.

> **Payoff is measured, not projected — independently reproduced from A-24's `per_seed_decomposition.csv`:** ΔQALY SD **1,200.1 → 360.2**, variance ratio **0.0901**, **11.1× equivalent seeds**.
> **The decisive evidence is A-24's sampler probe, verified in review.** At a one-person weight change, production `Generator.multinomial` gives mean L1 **0.351** and **max 188** against an expected shift of **0.0053**; the coupled inverse-CDF gives **0.016** and max 2. That is a direct demonstration of non-monotonicity.
> **REVIEW FINDING, load-bearing and not prominent in A-24: the two couplings are not independently useful and each alone makes the other channel WORSE.** Multinomial only → ΔQALY variance **45.8%**, background-death SD up to **133.5%**. Grouped background mortality only → ΔQALY variance **102.6%** (no improvement at all), debut SD up to **135.2%**. **The 9.0% exists only for the pair. Do not stage them across releases.**
> **REVIEW FINDING: the fix is contained.** `tests/test_a13_crn_key_compatibility.py` pins the **key derivation** — pickle payload, BLAKE2b digest, little-endian words, Philox stream at the key boundary — **not the multinomial algorithm**. A coupling built on the same keyed uniforms leaves every pin intact. **A failing pin means the implementation is wrong, not that the vectors need rebaselining.**
> **REVIEW FINDING: report but do not adopt the mean shift.** Base-case mean moves **956.2 → 1,150.7 (+20%)** = **0.63 SE** — unresolved, and exactly what an unbiased variance reduction looks like. Confirm at higher n before treating 1,150.7 as central. Also unresolved and worth watching: `infections_averted` variance **rises** in every counterfactual (120–154%), within chi-square noise at n=15.
> **Re-run scope, stated precisely.** Materially affected because they measure **paired arm differences**: **A-16** (highest value — the headline with SD 360 is a different result), **A-4**, **A-2**, **A-9**. **NOT invalidated** because they measure single-arm levels or deterministic-vs-stochastic-mean: **A-5a, B-8, A-20, A-21, A-22, A-23** — their exact numbers shift, their conclusions do not. **Do not treat a shifted digit as a changed finding.**

**A-28 RESULT (2026-09-14, Claude/Claude Code). Acceptance met. Report: `outputs/hta_results/a28_crn_coupling_fix_20260914_032810/A28_CRN_COUPLING_FIX.md`.**

Both couplings live in `counter_based_rng.py`, promoted from A-24's harness, not rewritten. **The production 15-seed base case reproduces A-24's `run_cf_both_base` BITWISE — all 65 decomposition columns × all 15 seeds, atol = rtol = 0.** ΔQALY SD **1,200.142 → 360.189 (variance 9.0%, 11.1× equivalent seeds)**; Δbirths SD 198.7 → 9.8; LY-via-debuts SD 880.8 → 32.7; LY-via-background-deaths SD 677.7 → 188.7. A-24's `class_key_all`, `class_cell_movement` and `class_sampler_rerandomisation` are **exactly 0.0 in every seed** — both defect channels gone, not reduced.

Controls, all met: **all four A-13 golden pins pass unchanged** (not rebaselined); distribution preservation is a **permanent** 37-test file, `tests/test_a28_coupled_samplers.py`, R = 20,000, reproducing A-24's `cf_sampler_validation.json` figures to 1e-9 against tolerances derived from R alone (4σ; no tolerance fitted); the **deterministic digest is unchanged** at `8d186b35…a455`, and tripwires prove deterministic mode never even constructs `CounterBasedRNG`; **full-suite failure/error identity set exactly unchanged** (58 failed / 5 errors, `problem_set_equal: true`, no new, no resolved, no status changes; 1,835 → 1,877 passed on 42 net new tests). Instrumented paired-run cost **+6.2%** (43.97 s → 46.71 s/seed). Equivalence to A-24's harness proven bitwise on 4,900 synthetic cases, with `binomial`/`poisson`/`random` on every non-background process bit-**unchanged**.

**One contract deliberately changed, flagged rather than buried.** `tests/test_a4_common_random_numbers.py::test_same_process_and_shared_tensor_coordinates_align_across_prep_expansion` failed on the first post-change suite: it pinned per-cell alignment under PrEP-axis expansion **using `adult_background_mortality`**, the one process A-28 was instructed to move off per-cell keying, and its fixture **doubles** the population (600 → 1,200) rather than relocating people. Measured: under pure redistribution of the same 600 people the grouped total is **exactly** unchanged (132 → 132), which is what the intervention actually does; under a genuine doubling the shared slice moves, which nothing in this model does. The assertion was kept and parametrised over the four processes that still key per cell, plus two new tests naming the new contract. **If the reviewer disagrees that this contract should lapse for this process, revisit A-24's branch-C diagnosis — do not adjust the test.**

**Two items reported as unresolved, per brief §5 — neither explained, neither acted on.** (i) Base-case mean **956.199 → 1,150.673 (+20.3%) = 0.628 pre-A-28 SE**. **NOT the new central estimate**; confirm at higher n. If it *resolves* at higher n the fix is not variance-neutral — a more serious finding. (ii) the HIV channel's variance rises: `infections_averted` SD 136.351 → 149.214 (**119.8%**) and — **not named in the brief, recorded here** — `ly_via_hiv_deaths_disc` SD 212.987 → 234.749 (**121.5%**). Both inside the n=15 two-sided 95% F region [0.336, 2.979].

**Self-review found one defect in the change and fixed it:** `_coupled_multinomial` silently gave the last category a negative count for negative `n`, where `Generator.multinomial` raised. Unreachable from any caller (both call sites guard `n > 0`), now raises `ValueError` with a test. All controls re-run after that edit; bitwise reproduction re-confirmed.

**Scope note:** `counter_based_rng.py` and `tests/test_a4_common_random_numbers.py` are **untracked in git** (single-commit repo; both created by the A-4/A-13 work and never committed), so the brief's "`git diff` reviewed — expected to change tracked files" is not satisfiable as written. Line-by-line diffs against pre-change snapshots are in `provenance/`. Nothing was committed.

**Re-run list (brief §5) with status.** **Read "RE-RUN REQUIRED" as "was completed, and its inputs have now changed" — every task below was already run and reported.** A-28 does not reopen them because they were unfinished; it reopens them because they are built from CRN-paired differences at n=15, which is the exact quantity A-28 changed (SD 1,200 → 360).

| task | prior run | status after A-28 | note |
|---|---|---|---|
| **A-16** stochastic headline economics | **COMPLETE 2026-09-11**, `outputs/hta_results/a16_stochastic_headline_20260911_030744/` | **RE-RUN REQUIRED — highest value, do next** | the headline at SD 360 is a different result from SD 1,200. A-28 §8 forbade computing a new ICER inside A-28; A-16 is where the headline is re-established. **A-16's own robustness argument survives regardless** — NMB is 88–99% cost, so ΔQALY could be an order of magnitude wrong without moving it. What A-28 can move is the **coherence ratio** (base case 1.1, every substitution scenario 183/−2.7/−9.3/−8.0/−10.9), which is a ΔQALY-per-averted-infection quantity and therefore directly downstream of the variance fix. |
| **A-4** CRN validation | complete (A-4, 2026-09-10) | **PARTIAL** | per-cell alignment criterion restated in `tests/test_a4_common_random_numbers.py`; the paired-variance criteria still need restating against the fixed sampler. |
| **A-2** variance decomposition | complete, `outputs/hta_results/a2_estimator_decomposition_20260913_235517/` | **DONE for the base case** | `run_prod_both_base/analysis/` is A-2's decomposition recomputed under the fix; residual composition confirmed. Switch levels not re-run. |
| **A-9** switch grid | **RUN 2026-09-13**, `outputs/hta_results/a9_switch_grid_process_decomposition_20260913_231232/A9_SWITCH_GRID_PROCESS_DECOMPOSITION.md` | **RE-RUN REQUIRED** | n=15 paired differences; the 2-SE increment criterion **may now resolve where it did not**, so this re-run can change the conclusion, not only the digits. Also answers A-24's dropped open escalation on seed-level monotonicity. **See the board-entry warning below — A-9's `[ ]` entry at §TRACK A is stale.** |
| A-5a, B-8, A-20, A-21, A-22, A-23 | all complete | **NOT invalidated, not re-run** | single-arm levels / deterministic-vs-stochastic-mean. **A shifted digit is not a changed finding.** |

> **STALE BOARD ENTRY, flagged 2026-09-14 during A-28, not rewritten.** The task-board entry `#### [ ] A-9 — Isolate deterministic substitution timing/allocation defect` (TRACK A) still shows `[ ]` under its **original** title and trigger (A-7's non-monotone infections-averted grid). The A-9 work that actually ran on 2026-09-13 was the **switch-grid process decomposition** specified in `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A9_SWITCH_GRID_2026-09-13.md`, and it has a report. The board entry was not updated when that run completed. **Anyone reading the board alone will conclude A-9 was never started, which is false.** Left as-is rather than rewritten because A-9's disposition is not A-28's to decide — but it should be reconciled before the board is used for planning.

##### `[ ] A-25/A-26/A-27 — scope as ONE age-structure workstream, not three fixes` — REVIEW RECOMMENDATION 2026-09-14

> **A-8's own inference is the reason and it is correctly labelled unverified:** the model gives young infections **too large** a loss (A-27, additive age-independent HIV mortality) while averting **too few** young infections (A-25 eligibility spans 15–110+; A-26 transmission has no age dimension). **1.911 may be approximately right in level partly because these cancel.** Fixing one in isolation could move the headline per-infection value materially in either direction.
> **Consequence for A-8's validation, stated plainly:** the 1.7-SE agreement between 1.911 and the cohort derivation is real but **both sides share the same age-independent mortality structure**. It is an **internal-consistency check, not an external validation**, and A-8's own "no usable Dutch or Western-European comparator" caveat compounds that. Both caveats must survive into the manuscript.
> **A-25 is the most consequential single item**, and it is **two** defects: the `"64-69"` typo, and the **silent fallback** in `age_to_idx.get(name, n_age_groups - 1)` which contradicts the project's own ENG-F04 rule. **Fix both** — the fallback will otherwise do this again elsewhere. It is present in every config in `configs/`, so it **predates and affects the submitted thesis**: a disclosure question of the same class as A-11.
> **Sequencing:** **A-28 first** — it changes no parameter and no age structure, only the noise, and is independent of all three. Then A-25/A-26/A-27 as a single workstream with **one combined before/after at fixed seeds**.

**Sources to acquire (A-8 §10, for `literature/`):** Farnham 2013 JAIDS full text; Trickey 2023 Lancet HIV + appendix; van Sighem 2010 AIDS full text; Nakagawa 2012 AIDS; May 2014 AIDS; Gueler 2017 AIDS; Positive Voices 2022 utility report; NICE TA1106 EAG utility/mortality sections.

---

#### `[ ] BATCH 2026-09-13 — four independent briefs, runnable in parallel`

> **A-22, A-9 and A-23 all reuse A-20's process-switch harness and its AST switch-surface inventory; A-2 is a different code path entirely. None depends on another's result.** All four are measurement tasks that implement nothing.

| task | brief | question |
|---|---|---|
| **A-22** | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_D14_BLOCK_GROUPING_2026-09-13.md` | How should the background-mortality block be grouped? Six levels, two of them falsification controls, with the A-21 identity and an ENG-01 guard re-run per level. **This is D-14's dial at the call site D-14 never swept.** |
| **A-9** | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A9_SWITCH_GRID_2026-09-13.md` | Which deterministic process makes the substitution switch grid non-monotone? A-20's decomposition pointed at A-15's symptom. **n=15, seeds 42–56** — matching A-15, *not* A-20's 7001–7050, so the grids stay comparable. |
| **A-23** | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A23_HIV_MORTALITY_ANOMALY_2026-09-13.md` | Why does stochastic HIV mortality alone inflate the model 122%? **Steps 1 and 2 need no new runs** — A-21 already retained an 881,280-row cell trace and A-20 retained 50 replicates. |
| **A-2** | `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_A2_ESTIMATOR_2026-09-13.md` | What is ΔQALY actually measuring, given r=+0.998 with person-years and +0.12 with infections averted? Also the task that can finally close **A-3**. |

> **Standing criterion-resolution rule applies to all four** — state the resolution of every threshold in the same sentence as the threshold. Prior occurrences: A-16 (CV<0.50 selected the wrong thing), A-19 (C1 could not certify its own winner), A-18 (passed at 4.57× SE), A-20 (passed).
> **Not in this batch and deliberately so: C-4.** It is a confirmed defect whose fix is a modelling decision plus a schedule audit, and it blocks the calibration rather than these diagnostics. It should be decided, not batched.

#### `[ ] C-5 — the 1975–1989 burn-in is unconstrained, and that is a DECLARATION problem, not a data problem` — OPENED 2026-09-14

> **The observation.** Every calibration target starts at **1990 or 1996** (`shm_*` 1996–2024; `ecdc_*` 1990–2017; all UNAIDS 1990–2023). **1975–1989 is scored by nothing.** In that window the model reports ~60 new AIDS cases/yr from **1975** — before AIDS was described (1981) and before the first Dutch case (1982) — and no diagnoses at all until 1985 (correct: no test existed).
> **Two parameters control the whole burn-in and both declare themselves unfinished.** `epidemic_seed.seed_prevalence` = 0.000712, tier `estimated`, bounds [0, 0.05], notes *"Placeholder value — must be fitted to Dutch epidemic trajectory. No reliable literature prior."* `epidemic_seed.msm_fraction` = 0.908, tier `estimated`, bounds [0, 1], notes *"Placeholder — must be calibrated. No reliable literature prior due to pre-1985 testing absence."* Both are tagged `calibration_target`; neither has been calibrated.
> **Rishi's framing, adopted: this is correct and unavoidable, not a defect.** Before 1985 HIV was unidentifiable — cases presented as AIDS-defining illnesses or not at all, and no contemporaneous MSM/non-MSM split exists. **No literature prior can exist for these parameters.** The right move is not to find a source; it is to declare them as **calibrated parameters with no literature prior**, give them wide honest bounds, and let history matching rule out what the later, firmer data cannot support.
> **What that requires, concretely, and it is a D-10/D-12 dependency:**
> 1. **Declare them in the paper as unidentifiable-by-literature and calibrated**, with their prior ranges stated. This is a strength of the history-matching framing, not a weakness — Andrianakis et al. exist precisely for parameters like these.
> 2. **Report an NROY range, never a fitted point.** If the NROY region for `seed_prevalence` spans most of its prior after several waves, that is an honest and publishable "not identified by these data" statement. **Fitting a point and presenting it as a finding is the failure mode to avoid.**
> 3. **Expect weak identification and say so.** With nothing scored before 1990, these parameters are constrained only *indirectly*, through the 1990 handoff state. The D-14 synthesis already showed the level gap is **mostly inherited** from the fork state (r 0.983–0.988), so the burn-in matters more than its lack of targets suggests.
> **One concrete lead worth checking before accepting "no constraint is possible."** **AIDS case surveillance is clinical, not test-dependent**, and ran in Western Europe from the mid-1980s. The register's `ecdc_aids_confirmed_cases` coverage starts at **1990** — but that may be the workbook's extract, not the source's extent. **Check whether ECDC/WHO-Europe holds Dutch AIDS case counts for 1982–1989.** If it does, that series is a *lagged* function of the seed and is the one honest anchor available for the burn-in. Worth one hour before concluding the window is unconstrainable.
> **Interaction with C-4.** Fixing C-4 changes the 1985–1995 diagnosis pathway substantially, which changes the undiagnosed pool, prevalence and transmission through exactly this window. **C-5's parameters must not be calibrated until C-4 is fixed**, or they will absorb the bug.

#### `[x] C-4 — MSM ARE NEVER DIAGNOSED BEFORE 1996` — **CODE FIXED 2026-09-14. The pre-1996 NUMBER remains open by decision.**

> **RESOLVED 2026-09-14 (Claude).** `run_v3.py:_build_msm_relative_diagnosis_schedule` now passes
> `default_before=1.0` — the neutral value of a multiplier. Regression test:
> `tests/test_a25_silent_default_guards.py::test_c4_msm_multiplier_defaults_to_the_neutral_value_not_zero`.
> Full analysis: `outputs/hta_results/c4prep_silent_default_audit_20260914_042723/C4_RECOMMENDATION.md`.
>
> **A fitted pre-1996 constant was evaluated and REJECTED.** A "derived" 1.273 (set so cumulative MSM
> ≤1995 matches SHM) fails four ways: the response is **sublinear** (elasticity 0.738, verified), so it
> misses the anchor it was built to hit (2,287 actual vs 2,463 linear-predicted; the true matching value
> is ≈1.42); it **overshoots the held-out ECDC 1990–95 series by 8.8%**; `ecdc_hiv_confirmed_cases@1990–1995`
> are **scored HM targets**, so hard-coding from SHM enters the same information twice and artificially
> shrinks NROY; and it asserts a **2.16× step down in MSM ascertainment at exactly 1996**, the year ART
> arrived. §1A.4 applies literally — the number existed only to make a target match.
>
> **THE RESIDUAL IS A REPORTED RESULT, NOT A DEFECT.** At 1.0 the model reaches **~79%** of SHM's MSM
> ≤1995 figure (1,646 ± 261 across 8 stochastic seeds vs 2,095 observed; **0/8 seeds reach it**;
> deterministic gives 92.4%, so deterministic-only evidence on this window runs optimistic). This is
> evidence the **pre-1996 base diagnosis anchors are too low**, which recalibration addresses.
>
> **Sequencing agreed:** ship 1.0 → **C-1** → one joint wave → re-test the residual against ECDC's
> 1990–95 *shape*. **Only if a residual survives**, add explicit 1985/1990 anchors to this schedule and
> calibrate them jointly — which also removes the 1996 discontinuity and makes `default_before` moot.
>
> **Scope correction:** `_build_diagnosis_schedule` also has `default_before=0.0` and the BASE schedule's
> first anchor is **1985**, so no MSM is diagnosed before 1985 under *any* option. The multiplier's live
> window is **1985–1995, 11 years, not 21.**
>
> **Effect size:** cumulative infections 1975–2025 move **30,238 → 24,781 (−18.0%)** stochastically,
> **36,664 → 27,670 (−24.5%)** deterministically. Every calibrated parameter shifts with it; a full wave
> is required.
>
> **Flagged, not acted on:** the fitted post-1996 series is 0.59 / **0.248** / 0.754. A multiplier of
> 0.248 in 2004 — MSM diagnosed at a quarter the non-MSM rate in the Netherlands — is implausible on its
> face and may itself be compensating for C-4. Re-examine after the wave. Also
> `component_weights.diagnosis = 5.0` weights an accounting quantity that has **no in-model mechanism
> before 1996**.
>
> **Deterministic digest changed** `0215d456…eeb8` → **`de6a2c63…4d80`**. `tools/run_a21_*` and
> `tools/run_a22_*` still assert the pre-A-25 value `8d186b35…a455` and will now fail. Left as-is and
> flagged: they are A-21/A-22 artefacts, not production.

**Original specification retained below.**

> **Found during review 2026-09-13** by code inspection plus A-20's retained deterministic baseline. Not yet written up as its own task; the fix is a modelling decision and is Rishi's.

**The observation, from output not inference.** `new_diagnoses_msm` in A-20's deterministic baseline is **0 in every year 1975–1995**, then **438 in 1996**. Total `new_diagnoses` *falls* 53 (1985) → 20 (1995) while PLHIV rises 1,240 → 3,757, then jumps to 455. The declining-diagnoses-while-the-epidemic-grows shape is the **non-MSM residual alone**.

**The cause.** `run_v3.py:_build_msm_relative_diagnosis_schedule` (line 1515) constructs its `TimeSchedule` **without passing `default_before`**, which defaults to **0.0** (`schedule_utils.py:26`; the class docstring at line 20 states *"Before first year: returns 0 (unavailable)"*). The MSM relative-diagnosis schedule's first anchor is **1996**. So for 1975–1995 the multiplier is **0.0**, `msm_annual_rate = annual_rate × 0 = 0`, and `_step_diagnosis` diagnoses no MSM at all.

**Why this is a bug and not a modelling choice — four independent lines.**

1. **The neutral value of a multiplier is 1.0, not 0.0.** This schedule is a *relative* modifier on a base diagnosis rate that is already live from 1985. Zero is not "no MSM-specific effect"; it is "MSM are undiagnosable."
2. **The same file handles the other multiplier correctly.** `run_v3.py:1720` (ART mortality reduction) **explicitly passes `default_before`**, set to the pre-existing constant. Same class of parameter, same file, opposite handling. That is an oversight, not a design decision.
3. **The 0.0 default is correct for the class's original purpose and wrong for this reuse.** `TimeSchedule` was written for **availability** schedules — does this thing exist yet? The **base diagnosis schedule starting at 1985 is correct**: the first-generation HIV antibody assay was not available earlier, so nobody could be diagnosed. PrEP schedules behave the same way, and `tests/test_diagnosis_year_bands.py:184` pins that behaviour deliberately. **The class is fine; using it unmodified for a multiplier is the error.**
4. **The config's own notes describe the intent and it is not this:** *"multiplies the base diagnosis schedule for MSM only; non-MSM groups use the base diagnosis schedule unchanged."* Nothing about MSM being undiagnosable.

**The historical record contradicts the model's behaviour.** The Amsterdam Cohort Studies have prospectively enrolled and tested MSM **since 1984**; the HIV antibody assay was available from 1985. **1996 is a data boundary, not an epidemiological one** — combination ART arrived in the Netherlands in September 1996, ATHENA was funded in 1997 and formally established in 1998, and SHM was created in 2000, which is why SHM publishes only a `≤1995` cumulative lump and why ECDC had to be used for annual 1990–95 values at all. The schedule anchor at 1996 reflects *when annual data begins*, not when MSM began being diagnosed. Sources: [ATHENA cohort profile](https://pmc.ncbi.nlm.nih.gov/articles/PMC6169757/), [Amsterdam Cohort Studies](https://www.amsterdamumc.org/en/research/institutes/amsterdam-public-health/strengths/aph-cohorts/acs-cohort-study.htm).

**The sources do not rescue it.** Model cumulative diagnoses 1975–1995 = **372**, of which **0** MSM.

> **CORRECTION 2026-09-14 (verified against the primary sources this session).** This paragraph previously read *"SHM's own published `≤1995` lump is **1,280**… the model undershoots by **3.4×**"*. **1,280 is the NON-MSM lump** — `Other men` 719 + `Women` 561 from SHM Table 1.1, the figure C-1 uses (see C-1's data note). It is not the total and must not be compared against total model diagnoses. **SHM HIV Monitoring Report 2025, Table 1.1, `≤1995` row, read directly: MSM 2,095 | Other men 719 | Women 561 | Trans 15 | <15y 53 | TOTAL 3,443.** So the model undershoots the national total by **9.3×**, not 3.4×, and the MSM-specific undershoot is total. ECDC/TESSy 1990–95, verified from `data/raw/validation/.../ECDC_HIVAIDS_NL_20260514_150705.csv` with the register's own filter: **2,602 total, of which 1,542 `Sex between men`**. The two sources are **consistent, not conflicting**: SHM MSM ≤1995 (2,095) − ECDC MSM 1990–95 (1,542) = **553** implied for ≤1989, a plausible rising curve across ~1982–89. Both are usable — SHM for the cumulative level, ECDC for the annual shape.

Whichever target is scored, a model that diagnoses zero MSM across the first two decades of a Dutch HIV epidemic is wrong.

**This REFRAMES D-11 and reverses the prior recommendation.** The register's concern — that scoring ECDC 1990–95 double-weights a quantity `shm_new_diagnoses_total` already covers — is a real weighting question **for 1996–2017**. For **1990–95 it is not a weighting question; it is a defect detector.** `ecdc_hiv_confirmed_cases@1990–1995` are the only targets anywhere with `I > 3`, their implausibility ramps monotonically **5.23 → 22.16** across exactly those six years, and **nothing exceeds 3 from 1996 onward** — the year the defect ends. **The history-matching machinery was working correctly and pointing at a real bug. Do not drop, demote or down-weight that series until C-4 is fixed and the wave re-run.**

**Relationship to C-1.** C-1 (non-MSM exogenous forcing) was the standing explanation for the pre-1996 shortfall. **C-4 is a different and larger defect at the opposite end:** the problem is not primarily that non-MSM are mis-modelled, it is that **MSM are not diagnosed at all**. C-1 remains open on its own merits but is **not** the explanation for these six targets. **Sequencing matters:** building C-1 first would have tuned a non-MSM forcing term to compensate for a defect in the MSM diagnosis path — moving the fit while making the model less correct.

**Candidate fixes — a MODELLING DECISION, not a one-line patch. None implemented.**

| option | what it asserts | cost |
|---|---|---|
| (a) `default_before = 1.0` | MSM diagnosed at the base rate until 1996 | no new parameters; asserts no MSM-specific ascertainment difference in the era when it was probably largest |
| (b) `default_before` = first anchor (0.5907) | the 1996 multiplier held since 1975 | no new parameters; asserts a 41% MSM deficit through the whole early epidemic |
| (c) **add explicit pre-1996 anchors and calibrate them** | era-specific MSM ascertainment, tied to testing generations and policy exactly as the post-1996 anchors are | adds free parameters; **most defensible, and consistent with how the base schedule is already justified.** Note V13.21 permits multipliers **above 1.0**, which the evidence may require — Dutch MSM were the most intensively tested group in the 1980s |

**Do (c) unless there is a reason not to**, and pre-specify the anchor years from testing-technology and policy eras before fitting, not after.

**MANDATORY alongside any fix — audit every `TimeSchedule` construction site.** There are five in production (`schedule_utils.py:185, 286, 287`; `run_v3.py:1513, 1538, 1720, 1959, 2227`) and **only one passes `default_before`**. Any schedule whose first anchor is later than the 1975 start year is **silently zero** before it. Classify each as *availability* (0.0 correct) or *multiplier/modifier* (0.0 wrong), and make `default_before` an explicit required argument so a silent zero cannot recur.

#### `[x] A-21 — Falsify or confirm rare-stratum starvation at the background-mortality call` — COMPLETE 2026-09-13. **CONFIRMED.**

> **Result:** `outputs/calibration/a21_rare_stratum_starvation_20260913_001500/A21_RARE_STRATUM_STARVATION.md`. One observation-only deterministic run (1975–2025, 204 quarterly steps, seed 7001) traced 12,240 live age–orientation background-mortality blocks and 1,224 HIV-mortality stage–ART calls. HIV-positive cells expected **8,029.743** background deaths but received **6,259**: allocation ratio **77.948%**, shortfall **22.052%**. The shortfall was **100% through 1985**, falling to **15.2% in 2025**; **97.802%** of occupied HIV-positive cells were sub-unit and **93.697%** received zero deaths. Every whole block preserved its rounded expected total exactly (maximum control error 0), and the HIV-negative excess balanced the HIV-positive shortfall after the block's ordinary rounding residual (maximum identity error `1.09e-12`). **This confirms Defect 1 at the background-mortality site and explains A-20's qualitative PLHIV-up / total-population-flat signature; it does not license an additive percentage attribution or a repair.** The side trace narrowed but did not explain A-20's adverse HIV-mortality arm: 10,282 deaths allocated versus 10,301.475 expected (**−0.189%**) at realised states, far too small to read arithmetically as the +122% hybrid level response; non-additive trajectory/compositional effects remain. Trace annual outputs match A-20's production-deterministic digest exactly. Eight focused tests pass; full-suite failure/error identity set unchanged (58 failures, 5 errors; 1,799→1,807 passes). No tracked production file or model input changed.

> **Independent review (`…_001500/ADDENDUM_2026-09-13_independent_review.md`): CONFIRMED, and stronger than the report claims.** Every headline number reproduces exactly and the conservation identity closes: shortfall **1,770.743** − HIV-negative excess **1,733.747** = block rounding **36.996**, so the whole shortfall is accounted for as relocation plus ordinary rounding, with nothing residual.
> **The time profile IS A-17's fingerprint, and the report does not connect them.** By era the HIV-positive shortfall is **97.3% (1975–1990)**, 54.8% (1991–2000), 28.3% (2001–2010), 14.9% (2011–2025) — and for **fifteen consecutive years, 1975 through 1989, HIV-positive people received literally ZERO background deaths** against an expectation of 119.85. A mechanism at ~100% strength through the exponential growth phase decaying to ~15% by the modern era is exactly "a constant per-step multiplicative bias accumulating through exponential growth and ceasing at saturation" — A-17's +0.0062/step linear rise to a 1990 peak then plateau. **A-17 said "nothing about allocation looks like that." This does.**
> **Scale check (plausibility, explicitly NOT a decomposition — A-21's prohibition on additive attribution stands).** Cumulative HIV-positive background deaths withheld: **1,770.7**. Deterministic − stochastic PLHIV gap at 2025: **+2,243**. The direct shortfall is **79% of the whole gap before any compounding**, and the background-mortality arm's total effect (−3,762.6 PLHIV at 2025) is 2.1× the direct shortfall — an ordinary amplification for a four-decade epidemic. **This is the first proposed mechanism whose magnitude does not have to be argued for** (Jensen was 46–154× too small; residual placement ~150× too small).
> **Why four previous allocation tests all came back negative — now fully explained.** A-14 and A-15 swept the *granularity dial* at the transmission/diagnosis/ART sites and **never touched the background-mortality call**. A-17 swapped the rule **globally**, and A-20 shows the allocation effect has **opposite signs across processes** (background mortality +0.139 vs progression −0.224, HIV mortality −0.611, transmission −0.146), so a global swap nets out negative. A-19 **proved** every size-monotone rule starves identically in the sub-unit regime — and A-21 now shows **97.8%** of occupied HIV+ cells sit exactly there, so changing the rule was never going to work. **The three refutations refuted three proposed mechanisms, not allocation itself.**
> **DEFECT 1 AND DEFECT 2 ARE THE SAME DEFECT, at one site.** That is a materially better position than "three refutations and an unexplained bias," and the documentation should say so.
> **Second finding in the HIV-mortality side trace, passed over by the report.** The aggregate is near-exact (−0.189%) but the **composition** moves: sub-unit AIDS-on-ART cells are **over**-allocated (101.716%) while chronic-on-ART cells are **under**-allocated (92.171%). A near-exact aggregate concealing cross-stage redistribution **is A-13's finding**, now observed directly at the mortality call — corroborating A-13 while confirming it is small in aggregate. A-20's +122% arm stays open.
> **What is now decidable.** The question is **D-14's, in its original form**: *"same event probability AND same downstream consequence."* Everyone in the block shares the background-mortality probability — first half holds. **Being alive with HIV has a different downstream consequence from being alive without it** — second half fails, so the HIV axis is not exchangeable at this call site and the block is drawn wrong. It is a **granularity decision, not a rule change** (A-19 closed the rule family by proof); it is **not on the manuscript critical path** (D-14 Rec 1 puts the base case on the stochastic mean, which has no allocation step for these events); and what it changes is **what can be written** — a checkable, localised, quantified disclosure instead of an unexplained 20% inflation.


**Original A-21 specification retained below for provenance.**

**One deterministic run with a trace hook. No sweep, no mode switch, no equivalence proof** — nothing is being switched, so A-20's harness infrastructure covers it as-is.

**The claim under test (A-20 review addendum §3, UNVERIFIED).** In deterministic mode, `deterministic_allocate_events` starves the HIV-positive cells at `demography.py:981/989` and relocates their background deaths to the HIV-negative cells in the same block, preserving the block total. That would inflate PLHIV while leaving total population unmoved.

**Method.** Instrument both call sites for a single deterministic 1975–2025 run on `configs/V13.22_20260604_nearfit_rank001_basecase.json`. Per call, record per cell: `n`, `p`, `expected = n·p`, `allocated`, and HIV status. Report per (year, age, orientation) block:

| quantity | prediction if the hypothesis holds |
|---|---|
| `Σ allocated` vs `round(Σ expected)` over the whole block | equal to ±1 — the allocator's own invariant, a control rather than a result |
| `Σ allocated` vs `Σ expected` over **HIV-positive cells** | allocated **materially below** expected, shortfall largest in the earliest years |
| the shortfall as a fraction of HIV-positive background deaths | **this is the size of the mechanism** |
| `Σ allocated − Σ expected` over **HIV-negative cells** | positive, **equal and opposite** |
| share of HIV-positive cells with `n·p < 1` | high early, falling as the epidemic matures |

**Point the same trace at the HIV-mortality call** while the hook is in place. A-20's HIV-mortality arm is its largest single effect (**+122% vs stochastic, −29.65 SE**) and sits in tension with **A-13**, which found deterministic mode *understates* HIV mortality. The two are not strictly comparable — A-13 measured a between-arm difference, A-20 a level — but the sign should be resolved and the marginal cost here is zero.

**Falsifiable in the strong sense:** if HIV-positive cells receive approximately `n·p`, the hypothesis is dead and A-20's "mechanism unidentified" verdict stands as written. **Report that outcome as prominently as a confirmation** — this is the fourth mechanism proposed for Defect 2 and three have been refuted.

**If it confirms, the follow-up is a modelling decision, not a bug fix.** The question becomes whether background mortality should be allocated across a block mixing HIV-negative and HIV-positive cells at one shared rate at all. That is **D-14's dial** — *"same event probability AND same downstream consequence"* — at a call site nobody has examined. The HIV axis plausibly fails the second half of that rule: being alive with HIV has a different downstream consequence from being alive without it. **Do not implement a change under A-21; A-21 measures.**




**Brief: `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_RESIDUAL_PLACEMENT_2026-09-12.md`.** Diagnostic, ends in a recommendation, implements nothing.

**Conditional — read before running.** This serves the **calibration**, not the headline. Under D-14 Rec 1 the base case moves to the stochastic mean and the deterministic allocator stops touching the headline; what it still touches is calibration, which runs deterministically thousands of times per wave in a mode B-8 shows runs 17–30% hot. **Run it if the paper needs the calibration to stand up; skip it if the plan is to document the bias and report a range.** A-16's result is robust to it either way.

**The suspect:** `deterministic_allocate_events` preserves `round(Σ expected)` exactly but awards residuals to the cells with the largest fractional expectation — systematically the larger cells. In a multiplicative system that redistribution compounds into a level bias. Would explain B-8's whole signature: opens in the 1980s growth phase, HIV-compartment-specific, erodes after saturation, untouched by any stratification. **Unsupported — the task tests it, it does not assume it.**

**Four steps, each cheap:** (1) extract a stochastic ensemble from **1975** rather than 1990 and locate the year the gap opens — *this alone can falsify the hypothesis*; (2) characterise the placement bias with no model at all, testing whether per-cell error correlates with cell size; (3) compete five allocation rules on a synthetic multiplicative process against the exact fractional trajectory, with cellwise rounding as the ENG-01 control that must fail; (4) implement the leading candidate as an opt-in diagnostic and test whether it closes B-8's gap.

**Likely outcome worth anticipating: stochastic rounding is unbiased per cell and therefore at every grouping, so if it wins, D-14's granularity question dissolves** — ENG-01 and A-13 resolve together. Honest cost: the allocated total stops being exactly `round(Σ expected)`, and "deterministic mode" becomes **"seeded-reproducible mode"**. A disclosure, not a defect.

**Two hard constraints on any candidate:** bit-reproducible at fixed seed (stochastic rounding must draw from `counter_based_rng.py`, never process randomness), and no reintroduction of ENG-01 rare-event erasure.

#### `[x] A-16 — The headline economics under the stochastic estimand` — COMPLETE 2026-09-11

> **DONE.** `outputs/hta_results/a16_stochastic_headline_20260911_030744/` (the `_030204` directory is an incomplete superseded attempt — do not cite). n=15, CRN-paired, seeds 42–56.
> **Result, robust (CONCLUSION survives, DIGITS stale 2026-09-14 — recomputed: −€557.2m [−574.8, −539.7] and −€4,619.5m [−4,638.3, −4,600.7]):** lenacapavir at list price is not cost-effective at any switch level or ZIN threshold. ~~NMB at €80k/QALY −€492.6m [−547.2, −437.9] (oral PrEP vs none) to −€4,548.6m (switch 100%); no CI comes within €398m of zero. **Robust because cost dominates** — |ΔCost| €569m–4,441m against |ΔQALY × 80k| of only €6–107m, so cost is **88–99% of NMB** and ΔQALY could be an order of magnitude wrong without moving it.
> **Threshold price collapsed. FIGURES STALE 2026-09-14** (post-change: CE threshold €1,831 at s10, undefined at s25-100; QALY-loss compensation €623/€254/€190/€104 at s25/50/75/100 — and the two are different quantities, never one range). ~~Pooled root at €80k: **€1,959** (switch 10%) → **€159** (switch 100%)~~, i.e. **0.4–4.8% of the €41,110 list price**, against the thesis's €9,987 (~24%). A **5–63× reduction**.
> **Corroborates A-11 independently — WITHDRAWN 2026-09-14.** ~~A-16's oral-PrEP ICER €595,113/QALY vs the thesis's €122,477 = factor **4.86**; A-11 put the health-effect overstatement at **4.06**~~ **Post-change ICER is €1,771,229/QALY = 14.46× vs A-11's 4.06×. The routes no longer agree; this is one route (A-11), not two.** by a different route.
> **Three traps when reading the table** (review, 2026-09-11): (1) **do not quote the conditional CE price** (€4,081 etc.) — it conditions on ΔQALY > 0, keeping only favourable seeds, so it is biased **optimistic**; use the pooled root. (2) **The CV < 0.50 stability verdict is not informative** — NMB passes only because NMB ≈ −ΔCost; its CV rises monotonically with WTP and converges to ΔCost's as WTP → 0, so the criterion rewards quantities containing *less* of the informative component. The criterion was specified in the brief and is the brief's error, not Codex's — it was pre-registered and applied faithfully. (3) **Do not simply raise n.** At switch 10%, ΔQALY +122.0 with 0.67 infections averted is **183 QALYs per averted infection** against a model value of ~1.03 — incoherent, so more seeds would yield *precisely estimated noise*. The coherence ratio is a better validity test than the CI: base case passes (1.1); **every** substitution scenario fails (183, −2.7, −9.3, −8.0, −10.9).
> **Confirms A-10 on the unbiased estimator:** every substitution scenario has positive infections averted and negative ΔQALY, so every substitution threshold price is conditional on the unsupported cabotegravir-proxy AE parameter.


**Brief: `documentation/archive/historical_workstreams/phase2/task_briefs/TASK_BRIEF_STOCHASTIC_HEADLINE_2026-09-11.md`.** A deliverable, not a diagnostic — it should end in a table that could be pasted into a results section.

**Why it exists:** every quantity computed under the CRN-paired stochastic estimand so far is epidemiological. **There is no stochastic ICER, cost, net benefit or threshold price anywhere in the project** (verified by search 2026-09-11). So the central economic quantities have only ever been computed in the mode that A-13/A-14/A-15/B-8 have shown to be biased three separate ways, and that D-14 Recommendation 1 proposes to abandon.

**The question it answers** is not "what is the ICER" — the parameter set still fails four gates and NROY is 0/4,000, so no economic result is defensible yet. It is **"does a stable headline quantity exist under an unbiased estimator, and which one?"** Rishi's prior is that the threshold price beats the absolute ICER because a ratio should be more stable; that has never been tested.

**Three methodological traps the brief specifies in detail:** do not average ICERs across seeds (ratio of means, not mean of ratios); ΔQALY straddles zero at switch_10 (+121.99, SD 366.98) so the ICER moves between quadrants and net benefit must be primary; and the two different quantities both called "threshold price" (F-4) must be named separately.

**Escalate if every candidate headline is as unstable as ΔQALY** (CV ≈ 1.25 at base case, ≈ 3.0 at switch_10). That is a real possible outcome and it changes the paper to *"a precise answer is not currently available, and here is what would resolve it"*.

**Feeds:** the paper-shape decision, which is the top open item in `STATE_OF_PLAY_2026-09-11.md` §4.

#### `[ ] A-14 / A-15 / B-8 — Allocation granularity` — SPEC WRITTEN 2026-09-11

**Full brief: `TASK_BRIEF_ALLOCATION_GRANULARITY_2026-09-11.md`.** Written for a multi-agent session; A-14 and B-8 start in parallel, A-15 follows A-14's harness, then a synthesis memo.

A-13 established that `deterministic_allocate_events` preserves the aggregate but not the within-stratum expectation, and that ENG-01 and A-13 are the two ends of one dial — too fine erases rare events, too coarse relocates them, and the fix for the first produced the second. **Allocation granularity is therefore an open modelling decision, logged as D-14**, and three currently-open tasks are downstream of it: A-2 (estimator redesign), A-9 (substitution defect), and the A-13 repair itself. Do not start any of those three until D-14 is settled.

- **A-14 — granularity sweep.** Opt-in stratified allocation harness (diagnostic only, production unchanged), swept over five levels from whole-tensor to fully cellwise, compared against A-4's 15-seed stochastic mean. Selection criterion, acceptance band and tie-break are pre-specified in writing before running; `delta_qaly` and the ICER are explicitly excluded from the criterion. **Pre-committed failure case: if no level passes, deterministic mode is not a valid approximation for this estimand and the base case moves to the stochastic mean with an interval.**
- **A-15 — does A-13 explain A-9?** Re-run the switch grid under two allocation levels and state whether the non-monotonic infections-averted sequence (+38, +7, +34, +113, +96) is restored. Closes A-9 or eliminates the hypothesis.
- **B-8 — is the calibration contaminated?** `new_aids_cases` is 28 of 92 tier-1 scored target-years (**30% of the calibration**) and is an AIDS-stage quantity, scored in the mode A-13 shows distorts AIDS-stage composition. Half the data exists: A-5a already holds the stochastic means (n=50) at a fixed config. **Direction is unknown and must be measured** — and note that if the bias suppresses AIDS-stage output, correcting it moves `new_aids_cases` *further from* its target, since the reachability audit already has it far above. A finding that the calibration gets harder is a valid finding.

### TRACK B — Configuration and calibration rebuild

**Blocks:** the plausible-fit ensemble, and therefore multi-fit robustness.

#### `[ ] B-1 — Resolve the canonical calibration space` (D-01a, CAL-01, CAL-03)

- **Problem:** `configs/calib_space.json` declares 27 free parameters. The sweep behind the reachability audit used `calib_space_v13_15_1975_orientation_seed_abc.json` with 32. ~~The rank001 particle carries parameters present in neither cleanly (`p_msm`, `msm_diag_mult_1996/2004/2019`, `seed_msm_chronic_share`, `seed_msm_aids_share`, `seed_non_msm_chronic_share`, `seed_non_msm_aids_share`). At least one bound also differs (`seed_msm_fraction` floor 0.8 in the sweep vs 0.5 in the default).~~
  > **CORRECTED 2026-09-14 (B-1).** Two of those clauses are wrong and the real blocker is different. Full analysis: `outputs/hta_results/b1_b2_calibration_space_20260914/B1_B2_CALIBRATION_SPACE.md`.
  > - **rank001 is not orphaned.** All 27 of A's parameters and all 32 of B's resolve in `V13.22_20260604_nearfit_rank001_basecase.json` and every value lies **inside** its declared box (`inside=27/27` and `inside=32/32`, `outside=0`). All seven parameters named above are present in B cleanly.
  > - **The spaces are disjoint extensions, not rivals:** 20 shared, 7 only in A (all `prep_oral_*`), 12 only in B (disease natural history + seed composition + `msm_diag_mult_*`). Union = 39.
  > - **Two bound conflicts, not one.** `seed_msm_fraction` A [0.5,1.0] vs B [0.8,1.0] (nested); **`seed_prevalence` A [1e-05,0.003] vs B [0.0005,0.005] is non-nested** and undocumented until now. rank001 sits in the intersection of both.
  > - **The real blocker is the start year.** A is written "for the production 1980 base config"; B fixes `simulation_start_year = 1975`. Enumerating all 18 configs: **no live 1980 production config exists** — every V13.x base case starts 1975 and is self-labelled `"status": "diagnostic-only"`. `run_history_matching.py:546` nonetheless defaults `--calib-space` to the 1980 space, and there is **no guard** that space and config agree (`:588-595`). Same silent-default class as C-1's `ConfigReader` drop.
  > - **B-2 has 0 role conflicts** between the two spaces; see the corrected B-2 entry below.
- **Task:** enumerate every parameter across all calibration spaces and the rank001 particle; produce one table showing where each appears, its bounds in each, and any conflicts. Then propose (do not unilaterally adopt) a single canonical space.
- **Acceptance:** the table exists; every discrepancy is named; a recommendation with rationale is written for Rishi to approve as D-01a.
- **Do not:** silently pick one. This is a scientific decision.

#### `[ ] B-2 — Reconcile parameter roles` (CAL-03, D-02)

- **Objective:** classify every parameter as fixed / calibrated / PSA-only, and reconcile with config tier tags, bounds, distributions and PSA eligibility.
- **UPDATED 2026-09-14 (B-2).** Cross-tabulating both spaces: **0 role conflicts** — every parameter appearing in both carries the same role in both. There is nothing to reconcile *between the spaces*. The live question is instead **identifiability after C-1**: now that non-MSM diagnoses are supplied exogenously, `seed_non_msm_chronic_share` and `seed_non_msm_aids_share` have lost the channel that identified them, and `p_msm` is narrowed to the MSM channel only. This is a structural argument from the target/output mapping, **not measured** — check it in wave 1 via NROY marginal width per parameter and demote any parameter whose marginal equals its prior. Separately, A's seven `prep_oral_*` parameters are constrained by **no target in the register** (`hm_target_register.json → prep_programme_targets_note`).
- **Why it matters more now:** under history matching the prior box *is* the space being excluded from, so an incorrect box invalidates the NROY set.
- **Note:** the four heterosexual/anal route betas are currently in `excluded_parameters` ("handled in PSA, not pre-PSA calibration"). That rationale does not travel into the v13_15 space. Resolve explicitly.
- **Acceptance:** tier-schema validation and a PSA dry-run pass **without relaxing any safeguard**.
- **Depends on:** B-1.

#### `[x] B-3 — Prototype the implausibility measure on existing sweeps` — COMPLETE 2026-09-10

> **DONE 2026-09-10.** Report: `outputs/calibration/hm_prototype_20260910/HM_BUILD_REPORT_2026-09-10.md`; artefacts in `raw/`; driver `tools/hm_prototype_rescore.py`.
> **Outcome:** NROY = **0 of 4,000** under `I_max` in both target sets (2 under `I_(2)`, 22 under `I_(3)` once non-MSM is removed). With non-MSM in, `shm_new_diagnoses_non_msm@2018` is binding for **4,000/4,000** and NROY stays 0 at a `V_disc` multiplier of **10** — structural, not a tolerance problem. Without it, `ecdc_hiv_confirmed_cases@1995` binds for 97.0%.
> **Three corrections to this task as written:**
> 1. The 20,000-particle sweep **cannot be rescored**: 196 columns, zero per-year simulator outputs. The prototype is 4,000 particles, not 24,000. Its path is also recorded relative to the project root, not the model dir (`../data/model-outputs/...`).
> 2. `seed_variability_summary.csv` **cannot supply `V_ensemble`**: it holds QALY / cost / infections-averted outcomes, no epidemiological series. `V_ensemble` = 0 for every target until **A-5**.
> 3. The Pukelsheim figure here and in spec §2.3 says 93%. It is **95.06%** (Vysochanskii–Petunin, 4/81); the distribution-free Chebyshev bound is 88.9%. Corrected in code, asserted in a test.
> **Escalations hit:** §5 bullet 2 (empty NROY) and spec §10 conditions 1, 2 and 3 — see §5 below.

- **Objective:** derisk the method change before committing to it. **This task has no dependencies and can start immediately.**
- **Method:** implement the implausibility measure and re-score existing particle CSVs. Same simulator evaluations, new acceptance rule, **no new compute.**
  - Sources: `data/model-outputs/v11_outputs/calibration/abc_smc_v13_22_linear_2019_recent_diag_solid_20260604/abc_smc_particles_20260604_065622.csv` (20,000 rows) and `outputs/calibration/abc_smc_v13_23_snpe_bootstrap/abc_smc_particles_20260822_205519.csv` (4,000 rows).
- **Formula:** `I(x) = |E[f(x)] − z| / sqrt(V_obs + V_code + V_ensemble + V_discrepancy)`
  - `V_obs` — from each target's published uncertainty. UNAIDS `Low/Mid/High` columns are in `../data/raw/validation/DiseaseData.xlsx`; sigma ≈ (High − Low)/3.92 read as 95%.
  - `V_code` — **zero.** No emulator; the simulator is run directly (~60s/run, 14 workers). Do not build a Gaussian process emulator; it is not needed at this runtime.
  - `V_ensemble` — from `outputs/hta_results/probe_diagnostics_20260906/raw/seed_variability_summary.csv` for now; replace with A-5 output when available.
  - `V_discrepancy` — must be **specified and justified in writing**. Andrianakis et al. use 10% of the variance of simulator output training data per wave, declared openly as illustrative. Do not pick a value silently.
- **Combination rule:** per-target implausibility, then take the **maximum** across targets — NOT a weighted sum. This is non-compensatory by design: a good fit on one target must not offset a failure on another. Also compute the second- and third-largest for robustness to a single badly-specified target, and report all three.
- **Cutoff:** rule out `I(x) > 3` (Pukelsheim's 3-sigma rule: any continuous unimodal distribution holds at least 93% of its mass within 3 SD of the mean).
- **Acceptance:** implausibility computed for every particle; NROY size reported under the max, 2nd-max and 3rd-max rules; **and the ensemble-size-vs-`V_discrepancy` curve produced** (this curve is the evidence the tolerance was not tuned to pass).
- **Reference:** Andrianakis et al. 2015, *PLoS Comput Biol* 11(1):e1003968 — HIV transmission model, 22 parameters / 18 outputs.
- **Expected finding:** non-MSM targets will rule out nearly everything under the max rule. That is correct behaviour, not a bug — it is the same fact the reachability audit found. Report it; do not tune around it.
- **SCOPE LIMIT added 2026-09-10 — this is a methods dry-run only.** Every existing particle CSV was generated with the **pre-fix `demography.py`** (cellwise rounding, corrected by ENG-01 and shown by A-11 to be materially defective). The simulated outputs in those files therefore come from a model that no longer exists. That is fine for testing whether the implausibility machinery behaves sensibly, and **not** fine for anything downstream: **no NROY set derived from these particles may be carried into B-6, B-7, or any result.** State this limitation prominently in the report. The real wave 1 must be re-run on post-fix code after B-1/B-2 settle the parameter space.

#### `[x] B-4 — Target register and epistemic tiering` (D-07) — COMPLETE 2026-09-10

> **DONE 2026-09-10.** `configs/hm_target_register.json` + `hm_target_register.py`: 11 series → 319 scalar (series, year) targets, each with a tier, a documented `z`, and a `V_obs` that is either derived or explicitly flagged underivable. Register table: `outputs/calibration/hm_prototype_20260910/raw/target_register_{scalar,series}.csv`.
> **Verified at source, not assumed:** UNAIDS low/high **are** 95% intervals (Methods annex JC3153_GAU25 Annex 1: the bounds "define the range within which the true value lies in 95% of cases"), so `/3.92` is justified. The same annex confirms the CSAVR tier-2 classification. The workbook's AIDS-death series is the **modelled** UNAIDS one, not vital registration — so tier 2 stands, and a raw CBS series would be a *new* tier-1 target, not a substitute.
> **Uniformity demonstration written** (spec §4.2): the rule demotes four gate families, of which `plhiv_gate` and `art_stock_gate` were **PASSING** in the submitted base case, `aids_death_gate` was passing with warnings, and only `incidence_gate` was failing. Two of the four constraints removed are ones the model already satisfied.
> **`V_obs` is underivable for five series** and this is the finding that matters: SHM carries a notification-delay handle only (σ = 73/34/18/10/5/3 for 2024…2019, exactly **0 for 23 of 29 years**) and **no ascertainment handle at all**; ECDC publishes no uncertainty; `UNAIDS-ART` has no low/high columns. Consequence: **all 92 tier-1 budgets are 100% `V_disc`.**
> **Not in the register — blocker for B-6/HM-5:** the four RIVM PrEP-programme series. They constrain six of the 27 parameters in `calib_space.json` (`prep_oral_init_2019..2025`, `prep_oral_dropout_*`), which are therefore unconstrained by any wave. The `PrEP-Historical` sheet is an analyst-built mapping layer (`target_status = mapped_base`), publishes no uncertainty, and its 2025 row is an H1 partial-year check. Deciding their tier needs the RIVM 2025 PrEP evaluatierapport read.

- **Objective:** one table, one row per target: series name, source, year coverage, whether it is an observed count or a model-derived estimate, `V_obs` derivation, tier, and role (calibration vs cross-validation).
- **Established tiering** (brief §3.4):
  - **Observed counts → calibration targets, tight tolerance:** SHM diagnoses (`Stiching 1.1 (Diagnoses)`), ECDC AIDS and HIV case reports.
  - **CSAVR-derived → cross-validation comparators, wide tolerance:** UNAIDS PLHIV, `new_infections`, ART. These are a Spectrum Bayesian back-calculation fitted to the same case-surveillance diagnoses and vital-registration deaths already used as targets, so they are **not independent evidence** and weighting them as separate targets double-counts the underlying data.
- **Required disclosure:** the tiering rule must be justified by source epistemics, applied uniformly, and adopted with an explicit statement that it was decided after the gate failures were known. **Demonstrate that it also reclassifies targets currently passing** — that is what shows it is not self-serving.
- **Acceptance:** the register exists; every target has a tier and a documented `V_obs`; the uniformity demonstration is written.

#### `[ ] B-5 — Retire arbitrary thresholds`

- **Targets for retirement** in `calib_acceptance_gates.py` and `calib_historical_shape_gates.py`: `incidence_recent_ratio_ceiling = 1.75`, `plhiv_recent_mape_ceiling = 10.0`, `art_recent_mape_ceiling = 10.0`, `diagnosis_floor/ceiling`, `aids_peak_to_recent_mean_floor = 2.0`, and the `historical_shape_settings` MAPE ceilings.
- Each is replaced by an implausibility computed from a variance budget, or explicitly retained with a written justification for the specific number.
- **Also retired:** `component_weights` in `calib_distance.py` (the weighted sum), the ABC-SMC tolerance schedule, the adaptive perturbation kernel, and the Toni et al. importance weights (UNC-02A).
- **Note:** UNC-02A is not wasted work — validating it proved the ABC implementation was correct, which is why the failure to accept particles is attributable to the model rather than to the inference code. Record that in the checklist rather than deleting the history.
- **Acceptance:** every retained threshold has a written justification; every retired one is removed or clearly marked inactive; tests updated.

#### `[~] B-6 — Wave loop` — MACHINERY BUILT 2026-09-10, NOT RUN

> **Built, not executed.** `run_history_matching.py`, `hm_diagnostics.py`, `configs/hm_wave_plan_template.json`, `tests/test_hm_wave_loop.py` (22 tests incl. an end-to-end loop on a synthetic simulator). Waves are reproducible from a SHA-256 manifest; NROY volume is reported per wave; the stopping rule lives in the plan file and is checked after every wave. `score_wave` **refuses** to accept a tier-2 comparator or the excluded non-MSM series as a target. The CLI refuses to run at all without `--acknowledge-prerequisites`, printing B-1 / B-2 / C-1 / post-fix as the reasons.
> **Still open before a production wave:** the dependencies below, plus **D-10** (scoring granularity — this changes the answer more than any variance term: 92 targets → NROY 0/4,000; recent 5 years, 10 targets → 2,272; latest year only, 2 targets → 2,347) and **D-11/D-12/D-13**.
> **A cost of the no-emulator decision that spec §3.2 does not mention:** Andrianakis et al. use the emulator to *generate* the next wave's design, not only to score it. Without one, implausibility is known only at evaluated points, so wave 2+ proposes from a bounding-box + kNN **superset** of NROY and wastes runs as NROY becomes non-convex. The loop reports `n_design_proposed` vs `n_design_after_thinning` per wave; if that acceptance rate collapses, **that is the timing demonstration spec §8.3 requires** before an emulator may be reconsidered — not a licence to widen anything.

- **Objective:** the history-matching wave structure. Wave 1 = space-filling design over the prior box + implausibility filter. Wave 2+ = Latin hypercube **inside NROY**, adding targets, iterating until NROY stops shrinking.
- **Reuse:** `run_lhs_calibration.py` / `run_lhs_pipeline.py` are the right base — history matching wave 1 is an LHS plus a filter. Do **not** build this on the ABC-SMC runner.
- **Acceptance:** waves are reproducible from a manifest; NROY volume per wave is reported; the stopping rule is pre-specified and stated.
- **Depends on:** B-1, B-2, B-3, B-4, and Track C's C-1.
- **If NROY is empty:** this is a **result, not a failure.** It says the model cannot reproduce Dutch surveillance data within a stated uncertainty budget. Report it immediately and stop — it changes the paper's framing and is Rishi's decision, not yours.

#### `[ ] B-7 — Freeze the NROY ensemble` (CAL-07)

- **Acceptance:** checksummed manifest; complete parameter rows with joint dependence preserved (never independent marginals); provenance and implausibility values for every member.

---

### TRACK C — Model scope: exogenous non-MSM

#### `[x] C-1 — Implement exogenous non-MSM forcing` (D-03) — **IMPLEMENTED 2026-09-14**

> **DONE.** `exogenous_diagnoses.py`, wired at `run_v3.SimulationEngineV3._step_exogenous_non_msm`,
> enabled in `configs/V13.22_20260604_nearfit_rank001_basecase.json`. Report:
> `outputs/hta_results/c1_exogenous_non_msm_20260914_061217/C1_EXOGENOUS_NON_MSM.md`.
> Digest `de6a2c63…4d80` → `c61a75eb…2015`. Suite `problem_set_equal: True`.
>
> **Acceptance met.** Non-MSM diagnoses 1996-2024: 78 (0.8% of observed) → **9,712 (100.8%)**.
> PLHIV 2024 +8,658, `on_art_stock` +8,361, `plhiv_female_15plus` 838 → 4,531. All by
> construction, none by tuning. **Zero free parameters.**
>
> **The no-transmission promise holds approximately, not exactly.** Deterministic mode gives
> zero endogenous non-MSM infections; **stochastic mode gives +93.4 across 15 seeds (non-zero in
> 15/15)** — a **~1.0% double count** against 9,634 injected, so the series reproduces at 101.1%
> rather than 100%. The deterministic zero was the artefact (sub-critical expectations round
> down). Small and not fatal, but **do not report it as zero**.
>
> **Deviation from the brief, deliberate.** Built as a NEW module rather than extending
> `migration.py`, whose own header declares it "a demographic correction layer only; NOT an
> epidemiological driver". This also avoided a live trap: `migrant_hiv.orientation_distribution`
> is **53% MSM**, so extending that pathway without resetting the key would have injected the
> stream into the MSM prevalence numerator.
>
> **Correction to this brief's data note.** The column is `Men (CALC)`, not `Other men (CALC)`
> — no such column exists. Values match, only the label was wrong.
>
> **Pre-1996 decision: `burn_in`, lump NOT distributed.** Stronger reason than "shape unknown":
> **1990-95 is a scored window.** `ecdc_hiv_confirmed_cases` is `calibration_restricted` with
> `restricted_years: [1990, 1995]`, scores `new_diagnoses`, and binds 97.0% of prototype
> particles. Distributing the lump there would relieve the single most binding target *by
> construction*.
>
> **FINDING — the deterministic allocator couples strata (DETERMINISTIC MODE ONLY).**
> MSM infections **+1,805 (+6.5%)** from 1996 in deterministic mode; **in stochastic mode the
> shift is −5.0 ± 14.9 (−0.0%), i.e. absent**, because the A-28 coupled samplers key per stratum.
> The headline is stochastic and is not contaminated. Routes, contacts and prevalence denominators are
> all orientation-block-diagonal, so this is not a transmission pathway:
> `deterministic_allocate_events` computes its target over the whole tensor and ranks residuals
> across all cells. Demonstrated in isolation. **Not created by C-1, only exposed by it** — any
> change to non-MSM stock does this. D-14 / A-17 territory. State it, or it reads as a result.
>
> **FINDING — silent default, same class as A-25 and C-4.** C-1 initially had no effect at all:
> `ConfigReader.disease()` copies an allowlist of four keys and silently discarded the block.
> Fixed, with guard tests confirmed to fail when the fix is reverted.
>
> **Stochastic confirmation DONE 2026-09-14** (`STOCHASTIC_CONFIRMATION.md`): **RNG neutrality
> PASS**, 15/15 bit-identical over 79 columns, worst diff 0.0 — C-1 consumes no randomness and
> cannot degrade CRN pairing. Two findings above were corrected by it.
>
> **Caveats.** The
> acceptance quantities are near-tautological post-C-1 (`plhiv_female_15plus` is essentially the
> running sum of the fed-in series) — improvement there is evidence about mortality and ART
> retention, **not** fit quality of the transmission model.

#### `[x] C-1 — original task text (for reference)`

- **THE DESIGN, precisely:** feed the **observed** SHM non-MSM diagnosis series in directly as an exogenous forcing function. These people enter the PLHIV stock and consume care costs, but generate **no endogenous transmission**.
- **THIS IS NOT MIGRANT SEEDING.** No migrant/domestic decomposition. No import-volume parameter. No pre-2018 invented quantity. The design takes the *total* non-MSM diagnosis count as observed, which is precisely why it requires nothing unobservable. If you find yourself estimating how many infected migrants arrived in a given year, you have drifted off the design — stop.
- **Data:** `../data/raw/validation/DiseaseData.xlsx` → sheet `Stiching 1.1 (Diagnoses)` → `Other men (CALC)` + `Women (CALC)`, annually 1996-2024, plus a `<=1995` lump of 1,280 (719 + 561). Reference values: 1996 = 259, 2003 = 535 (peak), 2012 = 339, 2020 = 168, 2024 = 199.
- **Do not use** the `migrant_hiv_entries` sheet as an input series. It is 322 flat for 2018-2021 ("Backcast from SHM average"), observed only for 2022 and 2024, with 2023 a residual — weak evidence, and superseded by this design.
- **Mechanism:** `migration.py` already injects pre-diagnosed people into the stock from 2018. Extend back to 1996 using the real series, and **route them through `new_diagnoses` instead of bypassing it** (the current pathway bypasses it entirely, which is part of why non-MSM diagnoses read as zero).
- **Pre-1996:** the `<=1995` bucket is cohort-reconstructed and under-ascertains people who died pre-ART or never entered care. Either distribute it by a stated rule or leave it in unscored burn-in. State which. Do not fit to it.
- **Acceptance:** the model reproduces the non-MSM diagnosis series by construction; `plhiv_female_15plus`, total PLHIV and `on_art_stock` all measurably improve; the three non-MSM gate failures are resolved by design rather than by tuning.

#### `[~] C-2 — Resolve the incidence-target mismatch` — **TRACTABLE; DOES NOT GATE C-1**

> **Verdict 2026-09-14.** C-2 does **not** block C-1 and did not. `unaids_new_infections_15plus`
> is `role: cross_validation`, and `run_history_matching.py:368-376` **refuses** to let a
> non-calibration series enter `I_max` (`tests/test_hm_wave_loop.py:168`). The feared failure —
> an all-group target scored against an MSM-only quantity corrupting NROY — **cannot occur in
> the HM machinery as built**.
>
> **Done:** `new_infections_msm` / `new_infections_non_msm` added to `calib_extractor.py`. The
> outputs already existed at `run_v3.py:322-323` but were never extracted, which is why nobody
> could see that endogenous incidence was **already 100% MSM before C-1** (verified: 0 in all
> 204 rows of `hta_baseline_prefreeze.csv`).
>
> **Option (b) — infection-to-diagnosis lag — should be rejected**, and not mainly on
> free-parameter grounds: shifting the non-MSM diagnosis series back by L asserts those
> infections occurred *in the Netherlands* L years earlier, which is what SHM excludes from its
> national incidence estimate. Repairing that needs a pre-arrival fraction — the migrant/domestic
> decomposition C-1 explicitly forbids.
>
> **Still open:** an MSM-specific incidence comparator exists only as a **chart** (SHM 2025
> Fig 1.3B) plus three anchors in the text (2010: 710 [680-745]; 2020: 175 [160-190]; 2024: 310
> [250-365]). No annual MSM incidence series exists in any data file the project holds.
>
> **Real exposure is elsewhere:** `calib_acceptance_gates.py:177` and `calib_distance.py:159`
> still score all-group `new_infections`. **Neutralise under B-5 before any post-C-1 ABC-SMC or
> LHS run.**

#### `[ ] C-2 — original task text (for reference)`

- **Problem:** with non-MSM exogenous, endogenous incidence becomes MSM-only, while the UNAIDS `new_infections` target is all-group.
- **Options:** an MSM-specific incidence comparator, or adding the exogenous stream back with an assumed infection-to-diagnosis lag (which would be an *assumption*, label it as such).
- **Acceptance:** one option chosen, implemented, and justified. **Confirm tractable before building C-1 at scale.**

#### `[x] C-3 — Post-2024 extrapolation rule` (D-08) — **IMPLEMENTED 2026-09-14, BASE CASE CORRECTED**

> **The brief's premise was false.** It justified freezing on 2020-24 because those years "are
> flat". Verified against the workbook: OLS slope **+10.30/yr (SE 3.71, t = +2.78)**. They are a
> COVID trough and a recovery, and their mean (189.6) is a level **no year in the series
> represents** — below 2018 (214), 2019 (251) and 2023 (212).
>
> **Changed:** default freeze window is now the recovery years **2022-2024 (mean 203)**. Set
> `freeze_window: 5` to recover the brief's anchor.
>
> **A fourth rule was added.** The brief's three are all flat or falling while the data's recent
> signal rises, so the set was **one-sided** and could not display upside uncertainty at all.
> `recent_slope` is the upper member, capped at 3x the frozen level.
>
> **Spread over the economic horizon (2025-2064; `freeze_year` 2025 + 40 years, so 100% of the
> analysis period is governed by this rule and nothing observed enters the ICER):**
>
> | rule | cumulative | 2064 |
> |---|---:|---:|
> | *(status quo before C-1)* | 0 | 0 |
> | trend (down) | 2,272 | 14 |
> | net_migration | 3,143 | 68 |
> | **freeze (base)** | **8,120** | 203 |
> | recent_slope (up) | 16,836 | 609 |
>
> **Spread: 7.41x.** Reported as a finding per this brief's own standard.
>
> **Residual caveat no window removes:** SHM's reporting-delay uplift on 2024 is **+22.1%**
> (163 reported → 199 used), ramping 0.0/0.4/1.2/3.0/4.8/9.3/22.1% across 2018-2024. The most
> recent point is the most provisional.
>
> **Data-note correction.** `NL_Population_Forecasts.csv` is total population in millions, not
> migration, and `positive_net_migration` is a column in the forbidden `migrant_hiv_entries`
> sheet. The runnable source is `demography.migration.quantity.year_specific`, complete
> 1970-2070 with no gaps.

#### `[ ] C-3 — original task text (for reference)`

- **Base case:** freeze at the recent level. 2020-24 are flat (168 / 171 / 198 / 212 / 199), so this needs no trend assumption.
- **Alternative 1:** scale to net migration, using CBS `positive_net_migration` and `NL_Population_Forecasts.csv`.
- **Alternative 2:** extrapolate the 2004-2020 decline. **Alternative only** — the trend reversed after 2020, so the choice of trend is a judgement call.
- **Note for the report:** an exogenous stream is identical in both arms and so cancels in the incremental analysis. This is a *structural* argument, independent of any computed result. If the spread across the three rules turns out **not** to be small, that is a finding — report it.
- **Acceptance:** base case implemented, both alternatives runnable, spread across the three reported.

---

### TRACK D — Freeze and reproduce (needs A, B, C)

#### `[ ] D-1 — Calibration-to-analysis pre-flight` (CAL-06)

Reject diagnostic configs, non-accepting fits, tier-invalid inputs, missing provenance, and version mismatches. **Acceptance:** tests prove the old rank001 config is rejected and a valid fixture is accepted.

#### `[ ] D-2 — Test suite to zero` (R0-03, R0-04)

Currently 1,507 passed / 59 failed / 2 skipped / 5 errors. Separate reorganisation/path failures from genuine regressions. Resolve the split-vs-legacy HTA parity failure and the `aids_on_art` sampling-contract failure explicitly. **Acceptance:** zero failures, zero errors, every skip documented.

#### `[ ] D-3 — Remaining stale paths` (R0-01)

`"Validation "` still appears in: `calib_data_loader.py`, `validation_data_loader.py`, `tools/audit_1975_burnin_population.py`, `diagnostics/aging_operator_poc.py`, `diagnostics/calibration_probes/run_seed_year_shift_diagnostics.py`, `diagnostics/demographic_backtest.py`. Also note `configs/model_final.json` is a stale v10-era config, not the V13 production config its name implies — a naming collision, not a moved file.

#### `[ ] D-4 — Run manifest` (R0-06)

SHA-256 of model/config/data, git commit and dirty state, environment, commands, seeds, timestamps, output paths. All runners write or reference the same format.

#### `[ ] D-5 — Freeze` (REL-01, REL-09, REL-10)

One production config + NROY ensemble + code + data, checksummed, in `results/thesis_final/<run-id>/`. Git: there is currently a single commit (`0ffdbd5`) with ~10 modified files uncommitted. The ENG-01 completion-log entry incorrectly reads "no git repo" — correct it.

**Do not freeze before A-3 passes.** Freezing a run whose ΔQALY is an artifact freezes the artifact.

---

### TRACK E — Analyses (needs D)

#### `[ ] E-1 — Multi-fit robustness` (REL-03) — HIGHEST VALUE ANALYSIS IN THE PROJECT

Run the CEA across the whole NROY ensemble. Report the **threshold-price spread**, not just the ICER spread. **Acceptance:** every ensemble member's result traces to the frozen manifest; the spread is reported for both the ICER and the threshold price.

**Why it matters:** if the threshold price is tight across NROY while the raw ICER swings, that is simultaneously the paper's headline and its answer to every calibration criticism. If it is *not* tight, the paper's framing changes — report immediately.

#### `[ ] E-2 — Complexity ablation`

Run the CEA with a static/simplified population; quantify what the demographic scaffolding bought. Reviewers will ask; a number is needed.

#### `[ ] E-3 — Structural sensitivities` (HTA-03, HTA-04, ENG-02)

Pre-specified set: event ordering (epi delta known at ~1-2%; the ICER delta is outstanding), transmission functional form, horizon (40y vs 60y/lifetime), discount rates, perspective.

#### `[ ] E-4 — Cross-model validation` (fills the ISPOR-SMDM gap)

Against UNAIDS CSAVR and Nichols et al. 2016. The known ~11x ICER discrepancy vs Nichols must be presented as cross-validation with an explanation, not as a defensive aside. **This task is epidemiological and can proceed before Track A completes.**

#### `[ ] E-5 — PSA and CEAC` (UNC-03, UNC-05)

Probabilistic over economic parameters (costs, utilities, discount rates); NROY members as scenarios for epidemiological parameters. Sample **complete fitted rows**, never independent marginals.

**Do not run a stochastic PSA until A-3 and A-4 pass.** At the pre-fix noise level, PSA output is Monte Carlo noise rather than parameter uncertainty — this is the confirmed cause of the >€170B tail draws.

#### `[ ] E-6 — Interim: cost per infection averted`

`cost_per_infection_averted` is well-conditioned in both run modes and usable now. Not a substitute for a CEA, but viable for internal decisions while Track A is in flight, and worth considering as a secondary reported outcome.

---

### TRACK F — Manuscript (NO CODE DEPENDENCY — highest priority in parallel)

#### `[ ] F-1 — Perspective label` (HTA-01, D-05) — HIGHEST SEVERITY ITEM ON THIS BOARD

The base case is labelled "societal" while productivity and informal care are disabled. This is a **CHEERS reporting violation** and can produce a desk rejection rather than a review comment. It is also the specific concern the second reviewer flagged before the defense.

- Relabel to **"healthcare payer plus patient time costs"** in config, output tables, and manuscript — identical language in all three.
- Run productivity loss as a sourced sensitivity (HTA-02). The machinery exists in `configs/hta_costing_config.json`, flagged `not_used_base_case_sensitivity_only`: friction-cost method, 16.4-week Dutch friction period (iMTA 2024 Table 6-1), stage-specific loss rates. Patient time wage is €39.88/hr (iMTA 2024 Table 6-2).
- **The evidence gap to disclose:** Tuiu et al. (2026) gives Dutch HIV labour-market outcomes stratified by diagnosis timing, not by the model's acute/chronic/AIDS stages. The crosswalk does not exist, which is why productivity could not be activated in the base case without an unsupported assumption. State this rather than implying the choice was arbitrary.
- Implement or explicitly scope out informal care.
- **Added 2026-09-10:** any productivity-sensitivity figures must be produced on **post-fix code**. Do not cite or reuse any societal-perspective number from the thesis — those are doubly superseded (wrong perspective label *and* built on the pre-fix demography defect that A-11 identified). The relabel itself is pure documentation and is unaffected.

#### `[ ] F-2 — Bias-direction table`

As a table, not prose:

| Assumption | Effect on estimated lenacapavir benefit | Direction on ICER |
|---|---|---|
| Non-MSM exogenous → no MSM→partner bridging credit | Understates infections averted | Against lenacapavir |
| Model incidence ~2.7x CSAVR estimate | Overstates avertible infections | Favours lenacapavir |
| Productivity losses disabled | Understates averted societal costs | Against lenacapavir |
| Non-MSM excluded from transmission pool | Understates epidemic size | Ambiguous |
| **Lenacapavir AE disutility retained on a cabotegravir proxy while oral PrEP AE disutility was removed as unsupported** | **Understates lenacapavir benefit — sign-determining for ΔQALY (see A-10)** | **Against lenacapavir** |

Closing claim: *the two largest biases act in opposite directions and the conclusion holds under either.* Not that they cancel — that the result survives both.

#### `[ ] F-3 — Scope-out paragraph`

State the rule: **model what the decision needs, plus what the figures need in order not to be false.** Then: PWID (historically significant in 1980s-90s Amsterdam, near-zero now, and a likely contributor to the missing 1990s AIDS peak — **flag as hypothesis, verify against the ECDC AIDS series before asserting**), sex work (absorbed into the observed non-MSM series), geography (no decision effect, no figure effect).

#### `[ ] F-4 — Fix the "threshold price" naming collision`

Two different quantities currently share the label: the NMB-flip price versus oral PrEP when ΔQALY < 0 (€49-2,063 in the probe), and the price for cost-effectiveness at €80,000/QALY (€9,987 in the thesis). Define both explicitly; never use the bare term.

#### `[ ] F-5 — Correct historical-period claims`

1975-1989 has **zero targets** (UNAIDS and ECDC start 1990; SHM's earliest is a `<=1995` lump; `scoring_start_year = 1990`). Write "35 scored years plus burn-in", never "50 years of Dutch HIV history".

**State the burn-in's purpose rather than only its limitation.** The 1975 start is a deliberate design choice: it aligns epidemic onset with the first HIV cases historically identified in the Netherlands, and absorbs uncertainty in disease progression and initial seeding that cannot be directly estimated. Written that way it is a defensible modelling decision with a stated function; written only as "the first 15 years have no targets" it reads as an oversight. Both facts go in — the purpose first, the limitation second, plus the A-6 result on how much the deterministic approximation costs during that period.

#### `[ ] F-6 — Price currency and literature scan`

Verify the €41,110/yr list price and the current EU access/pricing position; a stale price makes the headline stale. Scan for lenacapavir CEAs published since June 2026 — this changes the novelty claim.

#### `[ ] F-7 — Journal target and paper-count decision`

One paper (model + CEA, model description in supplementary) or two (methods/epi, then CEA). Affects how much of Tracks B and E is needed. **Rishi's decision, with advisors.**

---

## 4. Decision log — do not decide these unilaterally

| ID | Decision | Status |
|---|---|---|
| D-01a | Canonical calibration space (27 vs 32 params) | Open — B-1 produces a recommendation only |
| D-01 | Production start year | Open |
| D-02 | Fixed vs calibrated natural-history parameters | Open — B-2 |
| D-03 | Non-MSM strategy | **Decided: exogenous observed forcing.** Formalise in the log. |
| D-04 | Inference claim | **Decided: history-matched plausible-fit ensemble.** No posterior language. |
| D-05 | Primary HTA perspective | Open — F-1, highest severity |
| D-06 | Primary evaluation horizon | Open |
| D-07 | Target epistemic tiering + discrepancy specification | **Tiering DECIDED and implemented (B-4, 2026-09-10)**; discrepancy specification still open — see D-12 |
| D-10 | History-matching scoring granularity: every (series, year) pair vs anchor years vs a summary-statistic set | **Open — highest-priority calibration decision.** 92 targets → NROY 0/4,000; recent 5 yrs (10) → 2,272; latest yr only (2) → 2,347. Andrianakis used 18 outputs for 22 params. Must be declared before wave 1. |
| D-11 | Role of `ecdc_hiv_confirmed_cases` — 1990–1995 only, full 1990–2017 overlap, or cross-validation | **RAISED TO BINDING 2026-09-12 by A-18 probe 5.1.** It measures the same quantity as `shm_new_diagnoses_total` for 1996–2017, so scoring both reintroduces weighting through target selection; register default is 1990–1995. **At the near-fit point all six targets with `I > 3` belong to this series**, so it alone governs `I_max` and therefore NROY. Excluding it, **nothing is excluded at all** (max I 22.163 → 0.882 det, 0.700 stoch) and the run-mode verdict flips. **Review addendum §2:** those six ramp **monotonically** — 5.23 / 6.61 / 8.63 / 12.42 / 16.17 / **22.16** across 1990–1995 — and **nothing anywhere exceeds 3 from 1996 on**. A monotone ramp terminating exactly when the diagnosis pipeline comes online is a structural feature with a start date (**C-1**), not discrimination between parameter values and not a run-mode artefact. **Decide on what the series is, not on what it does to any verdict.** |
| D-12 | `V_disc` specification: fraction of ensemble output variance (Andrianakis) vs relative to each target's own scale | Open. The same nominal 10% gives a 4% tolerance on `ecdc_hiv_confirmed_cases@1995` (σ 25.4 vs z 584) and ~5,300% on `shm_new_diagnoses_msm@1996` (σ 19,591 vs z 370). |
| D-14 | Deterministic allocation granularity: which strata are treated as exchangeable by `deterministic_allocate_events` | Open — A-14 recommends, Rishi decides. Blocks A-2, A-9 and the A-13 repair. Brief: `TASK_BRIEF_ALLOCATION_GRANULARITY_2026-09-11.md` |
| D-13 | Proceed with `V_obs` = 0 for every tier-1 target, or obtain an ascertainment-uncertainty source first | Open. Zero biases towards OVER-exclusion, so any NROY is a subset of the true one. Either choice is defensible; leaving it undecided is not. |
| D-08 | Post-2024 non-MSM extrapolation | Open — C-3 |
| D-09 | Base-case treatment of lenacapavir adverse-event disutility, and whether the no-AE result is co-primary | **Open — A-10 complete; Rishi decision pending.** A-10 recommends reporting the no-AE result as co-primary without changing the current base case. |

---

## 5. Escalate immediately if

- A-1 refutes the QALY mechanism hypothesis (the diagnosis is wrong; next step unknown).
- ~~NROY comes back empty in B-3 or B-6~~ — **TRIGGERED 2026-09-10 in B-3.** NROY = 0/4,000 under `I_max`. Qualified: pre-fix particles, so this is a machinery result, not yet a scientific one. The *pre-C-1* zero is scientifically meaningful because it holds at every `V_disc` multiplier tested up to 10. Two further spec §10 conditions also fired: the `V_disc` curve is steep (NROY 0 → 3,991 over three orders of magnitude, i.e. the result is driven by the discrepancy choice rather than the data), and `V_obs` is underivable for five series. See `outputs/calibration/hm_prototype_20260910/HM_BUILD_REPORT_2026-09-10.md` §9.
- E-1 shows the threshold price is *unstable* across NROY (the paper becomes about why a precise answer is not currently available, and what evidence would resolve it).
- Any task requires inventing a parameter value.
- Any task appears to require reopening a path listed in brief §12.

## 6. Suggested next three tasks, in parallel

*Revised 2026-09-10 after A-4 and A-5a completed.*

1. **A-9** — isolate the deterministic substitution timing/allocation defect before treating the 100% switch scenario as trustworthy. A-4 deliberately did not touch this non-stochastic problem.
2. **A-3 targeted verification/redesign** — A-4 now supplies the repaired base-case evidence: criterion 1 passes (stochastic mean is 0.33 SE from deterministic), but the literal every-seed sign criterion still fails in 3/15 seeds. Run the CRN substitution grid only after accounting for A-9, then decide explicitly whether criterion 2 represents a meaningful stochastic acceptance rule or an unattainable zero-tail requirement. Do not silently weaken it. A-5b follows only after this decision.
3. **F-1** — highest-severity manuscript item, needs no code at all. Any productivity figures must be produced on post-fix code; no thesis societal-perspective number may be reused.

**A-8** remains a cheap parallel derived check on expected person-years per averted infection. **A-5a is complete** and its year-specific variances can now populate Track B; **A-5b remains gated by A-3**.

**A-13 is complete.** Its cross-stage deterministic-allocation mechanism is now the leading concrete hypothesis for A-9 and should be tested there rather than rediscovered from scratch.
