# Task brief — A-28: couple the two samplers that break CRN pairing

**Created:** 2026-09-14 · **Owner:** Rishi Malhotra
**Type:** **the first implementation task in this sequence.** Everything since A-12 has been diagnostic. This one changes production code.
**Follows:** A-2 (found the noise), A-24 (found the cause and pre-measured the fix).
**Payoff, already measured, not projected:** ΔQALY SD **1,200.1 → 360.2** at n=15 — **variance to 9.0%, equivalent to 11.1× more seeds.**

---

## 1. What is wrong

Common random numbers work because each draw is a **monotone** function of a keyed uniform: perturb the parameter slightly between arms and the draw moves slightly. Every sampler in `counter_based_rng.py` satisfies this — binomial and Poisson both go through an exact discrete inverse CDF on a keyed uniform.

**One does not.** `counter_based_rng.py:222–225`:

```python
def multinomial(self, n, pvals, size=None, stratum_id=None):
    call_key = self._reserve_call("multinomial", stratum_id)
    generator = self._generator((call_key, ()))
    return generator.multinomial(int(n), np.asarray(pvals, dtype=float), size=size)
```

`Generator.multinomial` uses sequential conditional binomials with **rejection sampling**. Its output is not monotone in `pvals`. `migration.py:1043` calls it with weights equal to the current adult age × sex counts, which differ between arms by a hair — and a hair is enough to switch the sampler onto a different rejection path.

**A-24's direct probe, at the smallest possible perturbation (a one-person weight change):**

| sampler | share identical | mean L1 | max L1 |
|---|---:|---:|---:|
| **production `Generator.multinomial`** | 94.45% | **0.351** | **188** |
| coupled inverse-CDF | 99.20% | 0.016 | 2 |
| *expected shift from the weight change itself* | — | *0.0053* | — |

The production sampler moves the allocation **66× more than the perturbation warrants on average, and up to 188 people in the tail.** Roughly 14,000 quarterly migrants are re-allocated between sexes and age bins as if on fresh random numbers.

**It is not a keying bug and Option B ("complete the keys") is moot.** A-24 verified **9,770,583** paired draws in the base case and **11,550,294** at switch-100 are **100% bitwise aligned**, transmission and HIV mortality included. `Δbirths_key ≡ 0` in every seed. The keys are correct; the sampler cannot propagate a small perturbation smoothly.

**The second defect, same family, different shape.** Adult background mortality is keyed **per tensor cell**. The intervention moves people between cells (mainly PrEP slices), so those people are re-randomised even though nothing about them changed. This is the branch-C "cell movement" mechanism, and it carries the other large variance block.

**Downstream chain, measured by A-2 and A-24:** migration weights → female stock → births (Poisson on female stock) → sexual debuts → **life-years via debuts = 61.4% of Var(ΔQALY)**; background-mortality cell movement → **another ~36%**.

---

## 2. Read first

1. `PHASE2_AGENT_HANDOFF_2026-09-09.md` §1A — binding standard. **§1A.2 (equivalence proof before refactoring) is the governing clause for this task.**
2. `outputs/hta_results/a24_entry_pathway_crn_drift_20260914_010031/` — report, `PRESPECIFICATION.md`, `raw/multinomial_sampler_probe.json`, `raw/cf_sampler_validation.json`, and **`provenance/a24_harness_as_used_for_counterfactuals.py`, which already contains working implementations of both couplings.** Do not reinvent them; promote them.
3. `outputs/hta_results/a2_estimator_decomposition_20260913_235517/` — the variance decomposition this fix targets.
4. `counter_based_rng.py` in full, and `tests/test_counter_based_rng.py`, `tests/test_a13_crn_key_compatibility.py`.
5. `migration.py:900–1050` — the call site and how weights are built.

---

## 3. The change

### 3.1 Coupled multinomial

Replace the body of `CounterBasedRNG.multinomial` with a **coupled inverse-CDF multinomial** — sequential conditional binomials, each drawn by the **existing keyed-uniform inverse-CDF binomial path already in this class**. Same keys, same uniforms, same distribution; monotone in the weights.

### 3.2 Group-level background-mortality coupling

Replace per-cell keying at the adult background-mortality call with a **distribution-preserving group-level coupling**, so that people the intervention merely relocates between cells are not re-randomised. A-24's harness implements this; promote it.

### 3.3 Apply them TOGETHER — this is not optional and the brief is explicit about why

**Independent verification of A-24's counterfactuals (recomputed from `per_seed_decomposition.csv`, n=15, base case):**

| configuration | ΔQALY SD | Var vs production | Δbirths SD | LY via debuts SD | LY via bg deaths SD |
|---|---:|---:|---:|---:|---:|
| production | 1,200.1 | 100.0% | 198.7 | 880.8 | 677.7 |
| **multinomial coupling only** | 812.6 | **45.8%** | 7.9 (0.2%) | 34.0 (0.1%) | 783.1 (**133.5%**) |
| **grouped background mortality only** | 1,215.5 | **102.6%** | 217.2 (119.5%) | 1,024.3 (**135.2%**) | 186.8 (7.6%) |
| **both** | **360.2** | **9.0%** | 9.8 (0.2%) | 32.7 (0.1%) | 188.7 (7.8%) |

**Each fix alone is close to worthless and each makes the other channel worse.** Grouped background mortality alone gives **no ΔQALY improvement at all** (102.6%). **Do not ship one and measure; do not stage them across releases.** The measured payoff exists only for the pair.

---

## 4. Controls — the acceptance bar

### 4.1 Distribution preservation must be PROVEN, not asserted (§1A.2)

This is the whole risk of the task: a coupling that changes the distribution silently changes the model. A-24's `raw/cf_sampler_validation.json` already demonstrates the standard — reproduce it as a **permanent test**, not a one-off:

- Coupled multinomial, R ≥ 20,000: empirical means and variances against theory. A-24 achieved max |z| on means **1.50** and max relative covariance error **1.43%**.
- Grouped background mortality, R ≥ 20,000: same, plus **off-diagonal correlation** — A-24 achieved max |z| **2.43**, max relative variance error **2.32%**, max |off-diagonal corr| **0.0145**.
- Both must also reproduce the **exact total** (`sum == n`) and be **bit-reproducible at a fixed key**.

### 4.2 The golden CRN pins must still pass unchanged

`tests/test_a13_crn_key_compatibility.py` pins the **key derivation** — protocol-5 pickle payload, BLAKE2b-256 digest, little-endian key words, and the NumPy Philox uniform stream at the key boundary. **It does not pin the multinomial algorithm.** A coupling built on the same keyed uniforms therefore leaves every pin intact. **If any of those four tests fails, the implementation has touched the key path and is wrong — stop and fix the implementation, do not rebaseline the golden vectors.**

`tests/test_counter_based_rng.py:87` requires exact total and reproducibility; a coupled inverse-CDF satisfies both.

### 4.3 Deterministic mode must be bit-identical

Deterministic mode does not use these samplers. Prove it: the deterministic 1975–2025 annual-output digest must remain **`8d186b359195f3a911e5d53577b0cc900273466694356e513a9284c40197a455`** — the value A-20, A-21 and A-22 all reproduced. **Runner raises if not.**

### 4.4 Reproduce A-24's counterfactual

With both couplings live in production, the n=15 base case must reproduce A-24's `run_cf_both_base` figures: ΔQALY SD **360.2**, Δbirths SD **9.8**, LY-via-debuts SD **32.7**, LY-via-background-deaths SD **188.7**. **Any material departure means the promotion from harness to production changed something.**

---

## 5. What must be re-run afterwards, and what must not be re-interpreted

**This changes every stochastic number in the project.** Be precise about what that does and does not invalidate.

**Materially affected — these measure PAIRED arm differences and the fix is exactly what improves them. Re-run:**

| task | why |
|---|---|
| **A-16** (stochastic headline economics) | **the highest-value re-run.** The headline with SD 360 instead of 1,200 is a different result |
| **A-4** (CRN validation) | its criteria are about paired variance reduction; restate them against the fixed sampler |
| **A-2** | the variance decomposition should be recomputed to confirm the residual composition |
| **A-9** | the switch grid is a set of paired differences at n=15; its 2-SE increment criterion may now resolve where it did not |

**NOT invalidated — do not re-interpret these.** A-5a, B-8, A-20, A-21, A-22 and A-23 measure **single-arm levels** or **deterministic-vs-stochastic-mean** comparisons. The coupling changes which stream a run uses; it does not change the distribution, so single-arm means and variances are unaffected in expectation. Their **exact numbers shift and their conclusions do not.** Re-run them for currency when convenient, and **do not treat a shifted digit as a changed finding.**

**One thing to watch and report, not to act on.** The base-case mean moves **956.2 → 1,150.7** (+20%). At production SD that is **0.63 SE** — not resolved, and exactly what an unbiased variance reduction looks like. **State it explicitly and confirm it at higher n before anyone treats 1,150.7 as the new central estimate.** Also unresolved: `infections_averted` variance rises in every counterfactual (120–154%), which at n=15 is within chi-square sampling noise. **Report both as unresolved; do not explain either.**

---

## 6. Acceptance

- [ ] Both couplings implemented **together**, promoted from A-24's harness rather than rewritten.
- [ ] Distribution-preservation tests permanent, R ≥ 20,000, at A-24's tolerances, for both samplers.
- [ ] All four A-13 golden CRN pins pass **unchanged**; exact-total and reproducibility tests pass.
- [ ] Deterministic digest unchanged and asserted in the runner.
- [ ] A-24's `run_cf_both_base` figures reproduced in production.
- [ ] Full suite run; failure/error identity set compared exactly against the current baseline (**58 failed / 1,826 passed / 2 skipped / 5 errors** after A-22).
- [ ] `git diff` reviewed: this task **is expected to change tracked files**, which no task since A-12 has. Every changed line explained.
- [ ] The mean shift reported with its resolution; the `infections_averted` variance rise reported as unresolved.
- [ ] Re-run list from §5 recorded in the handoff with each item's status.

## 7. Escalate immediately if

- Any A-13 golden pin fails. **The implementation is wrong; do not rebaseline.**
- Distribution preservation fails at either sampler. **Stop.** A coupling that changes the distribution is worse than the defect.
- The deterministic digest changes. **Stop** — deterministic mode should not touch these paths.
- Production reproduces A-24's variance reduction by less than half. The harness and production differ somewhere.
- The mean shift is **resolved** at higher n. That would mean the fix is not variance-neutral, which is a different and more serious finding.

## 8. Do not

- Do not ship one coupling without the other.
- Do not rebaseline any golden CRN vector.
- Do not change any parameter, rate, utility, cost, discount rate, horizon or allocator.
- Do not touch deterministic mode.
- Do not re-run or re-interpret A-5a / B-8 / A-20 / A-21 / A-22 / A-23 conclusions on the strength of shifted digits.
- Do not compute or publish a new ICER in this task. A-16's re-run is where the headline is re-established.
