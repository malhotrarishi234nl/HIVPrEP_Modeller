# Task brief — A-24: why do births differ between arms under common random numbers?

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

**Created:** 2026-09-14 · **Owner:** Rishi Malhotra
**Type:** diagnostic. **Implements nothing in production.** Ends in a mechanism, a variance attribution, and a recommendation on A-2's option B.
**Follows:** A-4 (built CRN), A-2 (found the drift and showed it dominates ΔQALY variance).
**Batching:** independent of A-8 (revised) — different question, different outputs. Can run in parallel.
**Model runs:** the instrumented runs specified below are diagnostic and **may be executed by the agent directly** (foreground, smoke test first). No calibration runs of any kind.

---

## 1. The observation

A-2's full run (`outputs/hta_results/a2_estimator_decomposition_20260913_235517/`, CRN-paired stochastic, seeds 42–56, 2025–2064) decomposed the base-case ΔQALY exactly (227 controls, max residual 4.6e-7):

| component (discounted) | mean | SD | share of Var(ΔQALY) | r with infections averted |
|---|---:|---:|---:|---:|
| infection-coupled (HIV deaths LY + HIV utility + ART decrement) | +1,592.7 | ≈276 | 2.40% | resolved, positive |
| LY via **sexual debuts** (entry pathway) | −29.8 | 880.8 | **61.4%** | −0.12 |
| LY via **background deaths** | −606.7 | 677.7 | **36.2%** | +0.03 |
| **total** | 956.2 | 1,200.1 | | |

And, per seed (A-2 §6.2, `run_full/step1_births_debut_channel.csv`):

- Δbirths (intervention − comparator) has SD **198.7** around mean **−7.3**; deterministic Δ is **+1**.
- r(Δbirths, LY via debuts) = **0.688 [0.271, 0.887]**; r(Δbirths, infections averted) = **0.037 [−0.484, 0.539]**.
- Seed 42: Δbirths is persistently **−5 to −17 per year from 2028**; debut effects start **2042** (`run_smoke/per_step_deltas.csv`).

**The tension to resolve.** Under CRN, a Poisson inverse-CDF on a shared uniform moves births only through λ = women × ASFR × dt × multiplier. The intervention averts HIV deaths, so if anything it should add women and add births, with Δbirths coupled to infections averted. Instead Δbirths is large, sign-symmetric, persistent within a seed, and uncoupled from the intervention. **Either the female stock is diverging for a non-HIV reason, or the arms are not actually drawing on the same uniforms.** A-2 did not test which. A-24 does.

---

## 2. Why it matters

1. **It decides A-2's option B.** Option B ("complete the CRN for the entry pathway") is a stochastic-path code change that invalidates A-4, A-5a, A-16 and A-2 bitwise. It is only worth doing if the drift is an alignment artefact. If the drift is real λ-driven variability, B cannot remove it and the only lever is more seeds (n≈60 halves SE). **A-24 must end with that decision made possible.**
2. **CRN validity.** A-4's variance reduction (SD 41,917.9 → 1,200.1) was measured, but whether every process is aligned was not. A misaligned stream is a CRN defect even if it is not a bias.
3. **Background deaths (36.2%) are the same question** one process over. Answer both.
4. **It now also governs the substitution scenarios.** A-9 (`outputs/hta_results/a9_switch_grid_process_decomposition_20260913_231232/`) found no coherent deterministic repair for the switch grid and recommends reporting that grid **from stochastic runs only**. Its all-stochastic reference is coherent (infections averted 0.67 / 29.33 / 56.87 / 89.47 / 122.53, minimum increment 4.56 SE; HIV-deaths grid monotone). If the entry/background-mortality drift exists in those runs too, that recommendation inherits it.

**Scope boundary with D-14 / A-22 / A-21 / A-23.** Those are all about **deterministic** integer allocation. A-22 (`outputs/calibration/a22_background_mortality_grouping_20260913_225630/`, canonical; the `_224437` directory is superseded) found no background-mortality grouping level that is material (best 0.10840 vs threshold 0.09419) and the escalation fired, so D-14 remains open. **A-24 is entirely about the stochastic CRN path, which has no allocation step, and is not blocked by D-14.** Do not import any deterministic-allocation explanation.

---

## 3. Read first

1. `PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A, and the A-4 entry.
2. `outputs/hta_results/a4_common_random_numbers_20260910_170048/A4_COMMON_RANDOM_NUMBERS.md` — how CRN was keyed, and `null_perturbation_hashes.csv`.
3. `outputs/hta_results/a2_estimator_decomposition_20260913_235517/A2_ESTIMATOR_DECOMPOSITION.md` §4.3, §6.2, §8; and `a2_decomposition.py` — **reuse its paired-run harness and controls; do not rebuild them.**
4. `counter_based_rng.py` — `CounterBasedRNG._reserve_call`, `_uniforms`, `poisson`, `binomial`.
5. `run_v3.py` — `_process_rng` (~line 480), `_draw_events` / `_draw_events_for` (~468–498), the timestep ordering (~653), and every `_process_rng("...")` call site.
6. `demography.py:calculate_births` (~682–749), plus the childhood pool, childhood mortality, aging and sexual-debut code.
7. `outputs/hta_results/a9_switch_grid_process_decomposition_20260913_231232/A9_SWITCH_GRID_PROCESS_DECOMPOSITION.md` §1–3, and its retained `runs/all_stochastic/seed_{42..56}/` — each has `timestep_simulation_deltas.csv` (per-step `delta_births`, `delta_background_deaths`, `delta_childhood_deaths`, `delta_net_migration`, `delta_total_population`, … for every switch level) and `event_flow_totals_long.csv`. **These are free data for §5a.**
8. `outputs/hta_results/a9_.../raw/switch_surface_inventory.csv` (A-20's AST inventory, 31 deterministic-branch sites) — a starting point for the call-site inventory in §4. It lists deterministic-branch sites, not every stochastic draw; extend it, do not assume it is complete.

---

## 4. Code facts to verify, not assume

These were read on 2026-09-14 while writing this brief. **Confirm each against the code before relying on it**; they are leads, not findings.

- **Call-index keying.** `CounterBasedRNG._reserve_call` increments `_call_index` on every draw. A draw's key is `(seed, year, timestep, process_id, distribution, call_index, stratum_id, ...)`. **If one arm makes an extra draw, or skips one, within a process-step, every later draw in that process-step gets a different key** and the arms stop sharing uniforms from that point.
- **Births skip draws conditionally.** `calculate_births` loops age bins and `continue`s **before** the Poisson call when `asfr == 0` or `women_count == 0`. The number of Poisson calls therefore depends on state. Likely identical between arms in practice — check.
- **Births take the `rng` argument**, not `_process_rng` directly. Establish which stream that is at each call (`birth_count`? something shared?) and whether it is re-created per step.
- **`unscoped_event_draw` is one shared process id** used by `_draw_events` for every legacy call site not routed through `_draw_events_for`. If the set or order of legacy call sites executed in a step differs between arms (e.g. a PrEP-state branch that only exists in the intervention arm), every later unscoped draw is re-keyed. **Inventory every call site that reaches it.**
- **Final-axis keying.** `_uniforms` keys each slice of the last tensor axis separately so arms with different PrEP-dimension sizes stay aligned. Confirm this holds for every tensor shape the entry and background-mortality pathways use, including 1-D and scalar calls (which take a different branch).
- **Cell-level CRN is not person-level CRN.** Uniforms are keyed per tensor cell, not per person. When the intervention moves people between cells (e.g. oral → lenacapavir PrEP slice, or susceptible → HIV+ one step later), the moved people are drawn on a **different cell's uniform** with a different `n`, so their background death, debut or ageing outcome is re-randomised even though every key aligns. **Lead from retained data:** in A-9's `runs/all_stochastic/seed_42/timestep_simulation_deltas.csv`, switch 10 at **step 200 (2025.0), the first intervention step**, already shows `delta_background_deaths = −1` and `delta_total_population = +1`, with 391 MSM moved from oral to lenacapavir that step and no infection or HIV-death difference. That is consistent with this mechanism and with key misalignment alike — it discriminates nothing yet, but it shows divergence starts immediately and outside HIV. **This mechanism would make the drift "aligned but still desynchronised": neither a key bug nor legitimate λ-driven variability. The Step 1 design must be able to tell the three apart (§5).**
- Other process ids seen: `birth_count`, `birth_sex`, `childhood_background_mortality`, `childhood_residence_aging`, `childhood_fractional_aging_legacy`, `continuous_sexual_debut`, `sexual_debut_assignment*`, `adult_background_mortality`, `migration`, `transmission`, `adverse_events`.

---

## 5a. Step 0 — retained data first. No new runs.

From A-9's `runs/all_stochastic/seed_{42..56}/timestep_simulation_deltas.csv` (five switch levels, lenacapavir substitution vs oral PrEP) and A-2's `run_full/` (base case, oral PrEP vs no PrEP):

1. **Does the drift exist in the switch grid?** Per seed × switch level: cumulative Δbirths, Δbackground deaths, Δchildhood deaths, Δnet migration; their across-seed SD; first step each becomes non-zero.
2. **Does it scale with how many people the intervention moves between cells?** Regress per-seed |cumulative Δbirths| and |cumulative Δbackground deaths| on switch level (a proxy for people moved between PrEP slices). Cell-movement re-randomisation (§4) predicts growth with switch level; a pure key bug need not. State the resolution of the slope at n=15 × 5 levels, allowing for within-seed correlation.
3. **Is it coupled to infections averted** within the switch grid? (Base case: r = 0.037.)
4. **When does it start?** Step 200 is the first intervention step. Report for each metric the share of seeds non-zero at step 200, and the first non-zero step per seed.

Report Step 0 before designing Step 1's instrumentation — it may narrow which processes to instrument.

---

## 5. Step 1 — alignment test. The decisive one.

**Pre-specify before running (write `PRESPECIFICATION.md` first, timestamped):**

Under correct CRN, for a given (seed, year, timestep, process_id, call), both arms consume **the same uniform**. That is a bitwise property, so the criterion needs no threshold:

- **Aligned** at a draw: the key tuple and the uniform are identical between arms.
- **Misaligned**: they differ.
- Report the **count and share of misaligned draws** per process, per era, and the **first misaligned (year, step, process, call)** per seed.

**Method.** Instrument without changing production behaviour — wrap or monkeypatch `CounterBasedRNG` in the diagnostic script so every draw logs `(arm, seed, year, step, process_id, distribution, call_index, stratum_id, shape, uniform digest, λ or (n, p), result)`. Hash uniforms per call rather than storing them if volume requires.

1. **Null control first.** Run the comparator against itself (identical arms). Every draw must align and results must be bitwise identical. If not, the instrumentation is wrong — stop and fix.
2. **Smoke:** seed 42, both arms, full horizon. Confirm the logged births reproduce A-2's `run_smoke` seed-42 Δbirths exactly.
3. **Full:** seeds 42–56, both arms, entry pathway (`birth_count`, `birth_sex`, childhood mortality, childhood aging, all debut processes), `adult_background_mortality`, `migration`, and `unscoped_event_draw`.

**Replay test for births (and debuts).** For each births call, recompute the intervention arm's births using **the comparator's uniform** and the intervention's own λ. Then:

- `Δbirths_λ` = Σ(replayed − comparator): the part caused by different women/λ with shared randomness — **legitimate**.
- `Δbirths_key` = Σ(realised − replayed): the part caused by different uniforms — **alignment artefact**.
- These sum to realised Δbirths exactly. **That identity is the control; if it does not close, stop.**

Report both per seed, their SDs, and each one's share of Var(Δbirths) with a bootstrap CI over seeds.

**Three-way classification.** The replay above separates key misalignment from everything else, but not the cell-movement mechanism in §4 from legitimate λ-driven variability. Add a second attribution for `adult_background_mortality`, sexual debut and childhood mortality: for each draw, split each arm's cell `n` into people **present in the same cell in both arms** and people **present in only one arm's cell** (moved in or out by the intervention, via the per-step state difference). Report the share of Δevents attributable to each. Then classify the total drift into:

1. **key misalignment** (different uniforms at the same key position);
2. **cell-movement re-randomisation** (aligned keys, but the intervention moved people between cells so their outcome was drawn on a different uniform);
3. **legitimate propagation** (aligned keys, same cells, different λ because the population genuinely differs).

The three must sum to the realised Δ exactly per seed. If the second attribution cannot be made exact at a draw (e.g. binomial inversion is not separable by sub-population), say so, report a bounded version, and state the bound's size.

Run Step 1 on the base case **and** on one switch level from A-9 (switch 100, the largest movement between PrEP slices), seeds 42–56.

---

## 6. Step 2 — follow whichever branch Step 1 selects

**Branch A — misalignment found (Δbirths_key carries material variance).**
- Name the mechanism: the first re-keyed draw, the process and call site that caused it, and why the arms diverge there (extra/skipped call, shared process id, shape branch, etc.).
- Quantify how far it propagates: does it re-key only that process-step, or everything downstream?
- Check whether `adult_background_mortality` and `migration` misalign through the same mechanism.
- **Estimate what option B would remove:** recompute ΔQALY (via A-2's decomposition) with the entry-pathway draws forced onto aligned keys **inside the diagnostic harness only**. Report the new Var(ΔQALY) and SD at n=15 with its CI. This is a counterfactual estimate, not an implementation.

**Branch C — cell-movement re-randomisation dominates.**
- Keys align, but the coupling is lost where the intervention moves people between cells. State which moves (PrEP slice changes, infection, diagnosis/ART changes) carry it.
- This is a **design limit of cell-keyed CRN**, not a bug. Say what would be required to couple those draws (e.g. drawing moved and unmoved sub-populations on separate keyed uniforms, or person/cohort-level keys) and its cost, **without implementing it**.
- Estimate what that option would remove, as in branch A (diagnostic harness only).

**Branch B — alignment holds (Δbirths is almost all Δbirths_λ).**
- The drift is real. Trace **Δwomen** of reproductive age upstream: per step, attribute the between-arm difference in the adult female stock to each process (HIV deaths, background deaths, migration in/out, debuts, aging) by accounting identity. The attribution must sum to Δwomen exactly.
- Explain the tension in §1: why is Δwomen **not** coupled to infections averted, and why does seed 42 go negative from 2028?
- Report which process carries it and whether that process is itself aligned (if it is aligned and still diverges, the divergence is legitimate stochastic amplification — say so).
- **Option B is then moot.** State the n needed for the target precision instead.

**Either branch:** apply the same Step 1 replay/attribution to **adult background mortality** (the 36.2%), so both non-HIV components are answered.

---

## 7. Resolution

- The alignment test is bitwise — no threshold, no resolution issue. Say so.
- Every variance share at n=15 is reported with a bootstrap CI and the statement of whether it is resolved.
- Any threshold introduced (e.g. "material variance") is stated **with its resolution in the same sentence**. Sixth occurrence of this lesson — do not add a seventh.

---

## 8. Acceptance

- [ ] `PRESPECIFICATION.md` written and timestamped before any full run.
- [ ] Null control passes (self-vs-self bitwise identical, 100% aligned).
- [ ] Seed-42 smoke reproduces A-2's Δbirths exactly.
- [ ] Every §4 code fact confirmed or corrected, with file:line.
- [ ] Step 0 reported from retained A-9 and A-2 data before Step 1 was designed.
- [ ] Per-process misalignment counts, shares and first-misaligned draw, seeds 42–56, base case and switch 100.
- [ ] Δbirths split into λ and key parts; identity closes exactly per seed.
- [ ] Three-way classification (key / cell-movement / legitimate) for births, debuts, childhood and adult background mortality, summing exactly (or bounded, with the bound stated).
- [ ] Branch A, B or C completed; background mortality answered in every case.
- [ ] A clear recommendation on option B vs a cell-movement coupling fix vs more seeds, with the expected variance reduction or required n, and its cost — including which existing evidence (A-4, A-5a, A-16, A-2, A-9's stochastic references) would need re-running.
- [ ] An explicit statement on whether A-9's "report the switch grid from stochastic runs only" recommendation is affected.
- [ ] Full suite run; failure/error set compared exactly against the current baseline (A-9's `full_suite_after.xml`: 1,835 passed / 58 failed / 2 skipped / 5 errors).
- [ ] `git diff` on tracked files unchanged (hash `7f034cf1…c166` at last check), or explained.
- [ ] Limitations: one parameter point, n=15, base case only (switch grid not examined).

## 9. Escalate immediately if

- **The null control fails.** Either the instrumentation or CRN itself is broken — stop.
- **Misalignment reaches the transmission or HIV-mortality streams.** That would contaminate the infection-coupled component that A-2 relies on, not just the noise. Stop and report.
- **Misalignment starts at the first intervention step in every seed.** That is a structural CRN defect, not a rare branch, and it affects every stochastic comparison since A-4.
- **The drift in the switch grid is large enough to change the sign or monotonicity of A-9's stochastic infections or HIV-deaths grids in any seed-level analysis.** That would undercut the only coherent substitution evidence the paper has.
- **No branch explains Δbirths.** Say so plainly.

## 10. Do not

- Do not modify `counter_based_rng.py`, any process id, any key, the CRN golden vectors, or any production call site. Instrument in the diagnostic script only.
- Do not implement option B, even if branch A makes it look trivial. It is Rishi's decision because it invalidates existing stochastic evidence.
- Do not change any demographic, fertility, mortality, utility, cost, discount or horizon parameter.
- Do not compute or report an ICER.
- Do not re-derive A-2's decomposition — reuse `a2_decomposition.py`. Do not re-run A-9's grid — its per-step deltas are retained.
- Do not apply any D-14 / A-22 / A-21 / A-23 deterministic-allocation explanation to the stochastic path.
- Write only into `outputs/hta_results/a24_entry_pathway_crn_drift_<YYYYMMDD_HHMMSS>/`.
