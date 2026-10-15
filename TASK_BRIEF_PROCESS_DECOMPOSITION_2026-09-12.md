# Task brief — A-20: process-by-process decomposition of the deterministic level inflation

> **HISTORICAL SNAPSHOT (reviewed 2026-10-16).** Retained at its original
> provenance path; not current task or run authority. Start at `documentation/INDEX.md`.

**Created:** 2026-09-12
**Owner:** Rishi Malhotra
**Type:** measurement. **Implements nothing in production.** Ends in a localisation, or in an explicit statement that the gap does not localise.
**Follows:** A-17, A-19 and A-18 — three proposed mechanisms, three refutations.

---

## 1. Why this task exists, and why it is not a fourth hypothesis

B-8 measured a defect nobody has explained: run deterministically, the model sits **above** the n=50 stochastic mean in **222 of 222** calibration target-years, median **+20.4%**, with the PLHIV log ratio rising **+0.0062 per quarterly step** almost linearly from 1975 to a peak of **+0.312 in 1990**, then plateauing.

Three mechanisms have been proposed for it and all three are closed:

| task | proposed mechanism | outcome |
|---|---|---|
| A-17 | residual placement in `deterministic_allocate_events` | **refuted** — wrong direction (−0.13% vs +17–30%), ~150× too small, and the verified-unbiased replacement made the gap worse |
| A-19 | some other deterministic apportionment rule | **closed by proof** — every size-monotone rule (largest remainder, Sainte-Laguë, D'Hondt, Huntington-Hill, Adams) starves rare strata identically in the sub-unit regime |
| A-18 | Jensen's inequality on a concave force of infection | **refuted twice** — the model never computes `1−(1−β)^n`; its transmission expectation is affine in the contact count, and the literal hybrid achieved **0.104%** of the required reduction |

**A-20 proposes no mechanism.** Guessing a fourth functional form after three refutations is not a method. This task instead **measures which process carries the bias**, by holding every process deterministic except one. The output is a localisation — a ranked list of processes by how much of the gap each one's stochasticity recovers — not an explanation. Naming the mechanism inside whichever process wins is a separate, later task.

**This does not block the manuscript.** Under D-14 Rec 1 the base case moves to the CRN-paired stochastic mean, which does not contain this defect. A-20 determines whether the **calibration** can be run deterministically, and whether the inflation is a **disclosure item** or a **repairable defect**. Treat it accordingly: it is worth doing well, it is not worth doing urgently, and a clean null is an acceptable outcome.

---

## 2. The two starting conditions, and where they come from

These are the reason this task is a nine-arm sweep and not a nine-arm sweep times fifty years. **Both come from the A-18 review addendum §4** (`outputs/calibration/a18_jensen_mechanism_20260912_205053/ADDENDUM_2026-09-12_independent_review.md`), computed at zero additional compute from A-18's own retained replicate file.

**(a) The divergence is in the non-transmission processes, and it opens at 1995.** A-18 ran an arm — `hybrid_transmission` — with the **entire transmission path stochastic and every other process deterministic**. Compared against the **fully stochastic reference** (the comparison A-18 itself did not make), PLHIV means over the same 50 seeds:

| year | 1980 | 1985 | 1990 | 1995 | 2000 | 2005 | 2010 | 2015 | 2020 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| difference | +0.2% | +4.5% | **−1.0%** | −12.8% | −19.7% | −23.8% | −27.0% | **−28.5%** | −27.8% | −25.7% |

The two arms differ **only** in that the non-transmission processes are deterministic in one. They agree to within ~1% through 1990 and separate monotonically from 1995. So whatever is left is located in the non-transmission processes, and it becomes visible in the era when diagnosis and ART come online.

**(b) It is not stochastic fade-out.** No seed collapses; the distribution is near-symmetric at every year (`(mean−median)/mean ≤ 1.5%`); dispersion matches the fully stochastic reference's own (CV 0.074–0.162 vs 0.12–0.14). The shift is systematic, so a per-process mean comparison is the right instrument.

**How strong is this evidence?** **Weaker than any of the three refutations, and it must be labelled that way in your report.** It narrows rather than eliminates; the channels are **not additive**, so no share of the gap has been attributed by subtraction; and it is one parameter point at n=50. It tells you where to look first. It does not tell you what you will find.

---

## 3. Read first

1. `PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A — binding standard. **§1A.2 (equivalence proof before refactoring) and §1A.4 (tuning prohibition) both bite here.**
2. `outputs/calibration/a18_jensen_mechanism_20260912_205053/A18_JENSEN_MECHANISM.md` **and its `ADDENDUM_2026-09-12_independent_review.md`** — the harness pattern, and §2's two starting conditions.
3. `outputs/calibration/b8_deterministic_target_level_comparison_20260911_032028/` — the gap being decomposed.
4. `outputs/hta_results/a13_hiv_mortality_trace_20260910_230000/A13_HIV_MORTALITY_TRACE.md` — **required before Step 1**, see §5.
5. `outputs/calibration/a5a_epidemiological_seed_variability_20260911_042330/` — the post-CRN reference ensemble. **The `…_20260910_162000` run is PRE-CRN and superseded: its means agree to <1% but its variances are ~35% larger. Do not use it.**

---

## 4. The harness — and the switch surface, which is wider than it looks

**Do not build a parameterised copy of the model.** A-18 copied one function and paid for it with a full bit-for-bit equivalence proof. The mode switch is already threaded through production; your job is to make it **per-process** rather than global.

**But it is not a single chokepoint, and assuming it is will silently produce null arms.** The most-used surface is:

```
run_v3.py:493   def _draw_events_for(self, process_id, n, p):
                    if self.deterministic:
                        return self._draw_events(n, p)          # -> deterministic_allocate_events
                    return self._process_rng(process_id).binomial(n, p)
```

`process_id` is a string or a tuple whose first element names the family, and this covers **progression, diagnosis, ART initiation and dropout, PrEP uptake and dropout, HIV mortality**, and the unscoped draw. **It does not cover everything.** A `grep` for mode branches in production modules returns:

| module | branch sites | how the flag arrives | processes |
|---|---:|---|---|
| `run_v3.py` | 4 (`:474`, `:481`, `:495`, **`:1205`**) | `self.deterministic` | the chokepoint, **plus `_step_aging_fractional_legacy` which branches directly** |
| `demography.py` | **13** | `deterministic` **passed as a call argument** | births, childhood mortality, background mortality, sexual debut, fractional ageing, activity distribution |
| `migration.py` | 1 + threaded | `deterministic` passed as a call argument | migration placement and integer distribution |
| `transmission_corrected.py` | 14 | `deterministic` passed as a call argument | transmission — **already parameterised by A-18**, reuse it |
| `adverse_events.py` | 1 (`:334`) | `self.deterministic` | HTA only; expect dormant in calibration — confirm, do not assume |

**Two consequences you must design around.**

1. **`run_v3.py:1205` does not use `deterministic_allocate_events` at all** — its deterministic branch is `np.round(pop * (1 − aging_fraction))`, a different rounding rule from every other process. Its stochastic branch calls `_process_rng(("adult_fractional_aging_legacy", age_idx))` directly, bypassing the chokepoint. **An ageing arm patched only at `_draw_events_for` would do nothing and report a false null.**
2. **`demography.py` and `migration.py` receive the flag as an argument from `run_v3.py`.** Forcing those processes stochastic means changing what `run_v3.py` passes at those call sites, not patching inside those modules.

### 4.1 Mandatory gate — the switch-surface inventory, before any arm is run

Produce `raw/switch_surface_inventory.csv` mapping **every** mode branch site in production modules to the process family it belongs to, and assert it is complete:

| column | meaning |
|---|---|
| `module`, `line`, `function` | where the branch is |
| `flag_source` | `self.deterministic` or the name of the parameter it arrives as |
| `process_family` | the arm that owns it |
| `deterministic_rule` | `deterministic_allocate_events`, `np.round`, or other — **state which** |
| `exercised_calls_per_run` | measured, not assumed |

**Every branch site must map to exactly one arm, and every arm must own at least one branch site.** A site that maps to no arm is an uncovered process and the sweep is incomplete until it does. **This inventory is the difference between a decomposition and a set of accidental nulls**, and it is the single most likely way this task goes wrong.

### 4.2 Four requirements on the harness, all testable

1. **Opt-in and restoring.** A context manager restoring every binding on exit, including after an exception, with tests asserting restoration. A-18 §7.3's harness already does this — copy the pattern.
2. **Forced-stochastic processes must draw from the CRN factory, not `self.rng`.** `_process_rng` returns `self.rng` whenever `self.deterministic` is true, so a naive patch gives forced processes an unkeyed shared stream and silently breaks CRN pairing across arms. **Assert in a test that a forced process draws from `self._crn_factory.for_process(year, timestep, process_id)`.**
3. **Coverage audit written to the output directory.** Call counts per family per run. Every family exercised with a count, or **declared dormant** with a zero and a note. This is the failure A-12's dormancy guards exist to catch.
4. **No production default changes.** `git diff` on tracked files empty at the end, or every entry explained.

---

## 5. Step 0 — two gates, both cheap, both before any arm is run

### 5.0a Equivalence control (mandatory, §1A.2)

The patched `_draw_events_for` must reproduce, **bit-for-bit over the full 1975–2025 window**, on `configs/V13.22_20260604_nearfit_rank001_basecase.json`:

| corner | requirement |
|---|---|
| **no family forced** | identical to a production **deterministic** run |
| **every family forced** | identical to a production **stochastic** run at the same seed |

Hash the annual-output frames and record both hashes in the report. **The runner must `raise`, not report, if either corner fails.** A hybrid built on an unverified patch is worthless.

Additionally, confirm the deterministic baseline reproduces **B-8** in all 222 scored target-years (max absolute difference 0.0) — A-18 did this and it should hold identically.

### 5.0b The A-13 pre-check — do this before building the sweep

A-13 already localised a deterministic defect to **cross-stage residual assignment in the diagnosis and ART calls** — the same two processes §2 points at, reached from a completely different symptom (a 12.4-SE HIV-deaths gap). **That is two independent lines pointing at the same place, and it is cheap to check whether they are the same thing.**

Read A-13's trace and answer, in the report, before running anything:

- Does A-13's mechanism act in the direction that would **inflate** levels, or deflate them?
- Scaled to the whole run, is it of order **+20%**, or of order the 0.1–1% that A-17 and A-19 found for allocation effects?

**If it is the wrong direction or 100× too small, say so and proceed to the sweep.** If it is the right direction and the right order of magnitude, **say that too, and run the diagnosis and ART arms first to test it** — but do not stop there and do not let it become a fourth adopted hypothesis without an arm behind it. This is a prior, not a finding.

---

## 6. Step 1 — the sweep

**Design.** One arm per process family. Each arm: that family drawn stochastically, **every other process deterministic**, n=50, seeds **7001–7050** (matching A-18 and A-5a so the ensembles are comparable), window 1975–2025, config `configs/V13.22_20260604_nearfit_rank001_basecase.json`.

**References.** Compare every arm against **both**: (a) the fully deterministic run, and (b) the n=50 fully stochastic ensemble. A-18's mistake was reporting only against (a); §2's whole result came from doing (b).

**Arms, in run order.** Run the first two before building the rest.

| # | arm | `process_id` families |
|---|---|---|
| 1 | **diagnosis** | `diagnosis_non_msm`, `diagnosis_msm`, `diagnosis_all_orientations` |
| 2 | **ART** | `art_initiation`, `art_dropout` |
| 3 | diagnosis + ART together | all of the above, jointly |
| 4 | disease progression | `progression_acute_to_chronic`, `progression_chronic_to_aids_off_art`, `progression_chronic_to_aids_on_art` |
| 5 | HIV mortality | `("hiv_mortality", stage, on/off art)` |
| 6 | background mortality | `adult_background_mortality`, `childhood_background_mortality` |
| 7 | births | `birth_count`, `birth_sex` |
| 8 | migration | `migration` |
| 9 | ageing & sexual debut | `sexual_debut_assignment*`, `continuous_sexual_debut`, `childhood_residence_aging`, `childhood_fractional_aging_legacy` |
| 10 | PrEP | `("prep_uptake", …)`, `("prep_dropout", …)` |
| — | transmission | **already done** — A-18's `hybrid_transmission`. Do not re-run; cite it. |

Also enumerate `unscoped_event_draw` and `adverse_events` in the coverage audit and state whether either is live on this config.

**Arm 3 is not redundant.** Arms 1 and 2 measure two processes separately; arm 3 measures them together. **If arm 3 ≠ arm 1 + arm 2, that is the interaction, and it is a result** — it is also the direct empirical demonstration that the non-additivity caveat is real rather than a formality.

### The success criterion, pre-specified before any result is viewed

**Per-arm gap recovery.** For each arm, the median absolute log ratio against the n=50 stochastic mean across the 222 scored target-years. Deterministic baseline **0.18838**. An arm is called **material** if it falls below **0.09419** — half the baseline, the same threshold A-17 and A-18 used, so all three are directly comparable.

**Criterion resolution, stated alongside the threshold as the standing rule now requires.** The median standard error of the per-year log ratio is **0.020609** (n=50, computed as `stochastic_se / stochastic_mean`; A-18 reproduced this independently). The 0.09419 threshold is therefore **4.57× the median SE** — it can discriminate.

**A second, finer threshold is needed here and its resolution is different.** A decomposition also wants to rank arms that do *not* individually clear half the gap. Rank them by **displacement** — the reduction in median absolute log ratio relative to baseline — and call an arm **non-null** only if its displacement exceeds **3× the median SE (0.0618)**. Below that an arm is **reported as indistinguishable from zero**, not as a small effect. *(For calibration: A-18's `hybrid_contacts` displaced 0.0000978, i.e. 0.0047× the SE — a true null.)*

**This is the third occurrence of the criterion-resolution lesson** (A-16's CV<0.50, A-19's C1, A-18's — which passed). **State the resolution of any further threshold you introduce, in the same sentence as the threshold.**

### The confound you must report, and the discriminator that partly resolves it

**Forcing a process stochastic does two things at once:** it draws that process's events, **and** it removes that process's deterministic allocation step. So a per-arm result measures Defect 2's contribution **jointly with** Defect 1's for that process. You cannot separate them by arithmetic and you must not claim to.

**But the direction discriminates.** A-19 established that largest-remainder **starves** rare strata — the row holding the ten smallest cells ends at **1.8%** of its exact total. Removing allocation therefore pushes rare-stratum levels **up from a starved baseline**, which is the same direction as the gap, so a *small* upward move is ambiguous. A-17 bounded the whole allocation effect at **−0.13%** against a gap of **+17–30%**, ~150× too small. **So an arm whose displacement is of order 1% or less is not separable from allocation removal and must be reported as such; an arm that moves the level by order 10% cannot be explained by allocation removal alone**, because no allocation effect of that size has ever been measured in this model. Say which side of that line each arm falls on.

---

## 7. Step 2 — only if Step 1 localises

Run this **only** if at least one arm is material, or if arms 1–3 are jointly material.

1. **Where in time.** Per-year displacement for the winning arm(s), 1975–2025. Does it open at 1995, as §2 predicts, or somewhere else? **A winning arm that opens in 1978 falsifies §2's starting condition and is a more interesting result than one that confirms it.**
2. **Where in the state space.** Which strata carry the displacement — age, orientation, activity, HIV stage, ART status.
3. **How much of the gap.** The winning arm's displacement as a fraction of the baseline, reported **per arm only**. **Do not sum arms into a percentage decomposition.**

---

## 8. Acceptance

- [ ] Equivalence control passed in both corners, bit-for-bit over the full window, hashes in the report; runner raises on failure.
- [ ] B-8 reproduced in 222/222 target-years.
- [ ] A-13 pre-check answered in writing before the sweep, with a direction and an order of magnitude.
- [ ] **Switch-surface inventory complete** (`raw/switch_surface_inventory.csv`): every mode branch site in `run_v3.py`, `demography.py`, `migration.py`, `transmission_corrected.py` and `adverse_events.py` mapped to exactly one arm, with its deterministic rule named; every arm owning at least one site.
- [ ] Coverage audit: every process family exercised with a call count, or declared dormant with a zero.
- [ ] Criterion and its resolution stated **above** the results, not after.
- [ ] Every arm compared against **both** the deterministic run and the stochastic ensemble.
- [ ] Arms ranked by displacement, with the 3×SE null band applied and nulls named as nulls.
- [ ] The allocation-removal confound reported per arm, with each arm placed above or below the separability line.
- [ ] Arm 3 compared against arms 1+2, and any interaction reported.
- [ ] Every figure reproducible from a command written in the report.
- [ ] Full test suite run; failure set compared against the current baseline (**58 failed / 1,768 passed / 2 skipped / 5 errors** before any new test file) and any change explained.
- [ ] `git diff` on tracked files empty, or every entry explained.
- [ ] Limitations stated: one parameter point, n=50, and the non-additivity of channels.

---

## 9. Escalate immediately if

- Either equivalence corner fails. **Stop; do not report arms.**
- The coverage audit shows a family with zero calls that you expected to be live.
- A mode branch site maps to **no** arm, or an arm owns **no** branch site. **Stop and fix the arm definition** — do not run a sweep with an uncovered process, and do not report an arm whose switch was never reached.
- **No arm is material and no arm is non-null.** This is a real possible outcome and it is a finding: the gap would then be a property of the *composition* of deterministic processes rather than of any one of them. **Report it plainly as "the gap does not localise to a single process" and stop.** Do not start guessing again — that is the failure mode this task exists to end.
- An arm overshoots the stochastic reference the way A-18's `hybrid_transmission` did (−27.9%). Report the profile, do not convert it into a mechanism.
- The A-13 pre-check suggests A-13 explains the whole gap. **That would be a significant result and it needs an arm behind it before it is claimed**, not a trace reading.

---

## 10. Do not

- **Do not sum arms.** The channels are not additive. Arm 3 exists to demonstrate this.
- **Do not propose a fourth mechanism** on the strength of a localisation. Localising to a process is not the same as naming what that process does wrong, and the distinction is the whole point of this task.
- **Do not adjust the criterion, the null band, the seed set or the window after seeing results.** §1A.4.
- Do not change any production default, rate, parameter, tolerance or bound.
- Do not use the pre-CRN A-5a directory (`…_20260910_162000`).
- Do not re-run transmission — cite A-18's arm.
- Do not build a parameterised copy of any function whose mode branch you can reach through the existing flag.
- **Do not assume `_draw_events_for` is the only switch.** It is not — see §4. An arm patched only there will silently null out ageing, births, migration and background mortality.
- **Do not report a favourable arm more prominently than an unfavourable one**, and do not leave an unexplained profile out because it did not fit.
