# `chronic_to_aids_on_art`: bounded evidence check and decision options

> Archived under `documentation/reports/calibration/`; original filename retained.

**Scope (bounded):** the stored literature corpus only. One question: what population the rate represents, and whether its value is supported. No model change, no runs.

## 1. What the model does

- Chronic→AIDS at the annual probability `chronic_to_aids_on_art` applies to **everyone on ART** (`run_v3.py`, on-ART Chronic pool, all diagnosis states). The model has no viral-suppression or time-on-ART state.
- Value: 0.0040 in the base case used for the diagnostic waves (`V13.22_20260604_nearfit_rank001_basecase.json`) and 0.011 in `V13.22_20260604_final_candidate_rank012.json`. Prior box: 0.001–0.015. (Correction: an earlier chat message called 0.011 the base-case value.)
- Config source: "NEED DIST BLOCK????? Mocroft et al. 2010 … ~10-fold reduction vs untreated". Mocroft 2010 is a cause-of-death paper and is not in the corpus. The value is **Unsupported**.
- **The parameter can only be an effective rate for all ART person-time**, whatever its description says. A suppressed-only estimate cannot represent it directly (Rishi, 2026-09-16).

## 2. Evidence found (SHM HIV Monitoring Report 2025, `literature/STICHTING(2025)-HIVMonitoringReport2025NL.pdf`)

AIDS is defined as CDC category C. CD4 < 200 without an AIDS-defining condition does **not** count (printed p.179 / PDF p.176). This matches the model's clinical-AIDS compartment.

| Anchor | Population | Estimate | Location |
|---|---|---|---|
| A. First AIDS-defining events | All 32,093 adult PWH registered by SHM, after entry into care | 120.6 (108.1–134.2) per 1,000 PYFU in 1996 → **6.2 (5.2–7.4) in 2024** | printed p.184, Fig. 6.1B (intermediate years plotted only) |
| B. CDC-C events | On ART ≥ 1 year with undetectable/blip viral load at the event, 2000–2024 pooled | **5.88 (5.61–6.17) per 1,000 PYFU**; 1.77 (CD4 750+) to 241 (CD4 0–50) | printed pp.185–186, Table 6.2 |

Fit to the model parameter:
- **Anchor A** is the closest construct to the model's in-care progression: first clinical AIDS among people in care, both on and off ART, with a person-time denominator. *Well-supported* as a recent-era reference (2024 only).
  - Unverified: whether its numerator includes AIDS at entry into care. SHM's 765 first events in 2020–2024 are of the same order as RIVM annual AIDS totals (163/138/138 in 2020–22), which suggests entry events may be included. That is an inference and *Weakly supported*. If they are included, Anchor A **overstates** in-care progression.
- **Anchor B** excludes the first ART year and viraemic periods, both higher risk. It is restricted by viral load, pooled over 25 years, and may count more than one event per person.
  - It is a **suppressed-population reference, not the effective all-ART rate.** The effective rate should plausibly be at least as high, but the direction of the event-counting difference is unknown.
- **Not available in the corpus:** year-specific values between 1996 and 2024. Figure 6.1B is plotted without a table. Nothing supports a single constant rate across 1996–2025.

## 3. Direct comparison (Derived, from the decomposition run)

Model in-care rate = diagnosed Chronic→AIDS events per diagnosed Chronic PY, on and off ART, all groups. This is the model analogue of Anchor A, **excluding** AIDS at diagnosis.

| `chronic_to_aids_on_art` (per 1,000 PY) | Model in-care rate 2024 | 2010 | 2000 |
|---:|---:|---:|---:|
| 1.9 (rows 205, 351) | 3.0–3.5 | 5.7–6.4 | 12–14 |
| 2.9–5.2 (rows 203, 130, 396, 248) | 4.2–6.9 | 5.7–8.9 | 14–31 |
| 8.4 (row 102) | 9.9 | 11.1 | 17.7 |
| 11.4–14.4 (rows 191, 43, 261); the 0.011 value (final_candidate_rank012) is in this band | 11.8–14.6 | 14.7–19.2 | 18–29 |

- **Recent era.** On-ART values around 3–5 per 1,000 PY give model in-care rates inside SHM's 2024 interval (5.2–7.4). Values ≥ 8 give about 1.6–2.4× the SHM 2024 estimate. The 0.011 value (final_candidate_rank012) sits in that range; the nearfit base case (0.0040) does not. *Well-supported* for 2024, conditional on Anchor A excluding entry events; if it includes them, the supported values are lower still.
- **Earlier eras.** The model's in-care rate in 2000 (12–31) is far below SHM's 1996 figure (120.6), but those are different years and the gap is not comparable. No year-matched SHM value exists in the corpus.

## 4. Decision options (Rishi to choose; no default applied)

- **A. Constant effective rate, evidence-trimmed range.** Keep one rate for all ART person-time. Shrink the prior to about **0.002–0.008** annual probability, so model in-care rates cover SHM 2024 (5.2–7.4) with allowance for the entry-event ambiguity.
  - Documented limitation: one rate cannot represent the higher early-ART-era risk, so AIDS agreement before about 2005 may be sacrificed.
  - Needs no code change.
- **B. Two-era effective rate.**
  - Recent era (e.g. ≥ 2010): range as in A.
  - Early ART era: wide, **explicitly uncertain** range, because no year-matched evidence is stored.
  - Adds one parameter and a schedule, which is a small code change with a guard test.
  - Better matches the declining SHM curve, but the early range is *Unsupported* beyond "higher".
- **C. Keep the current box (0.001–0.015) and let the focused run find the region**, reporting the SHM comparison afterwards. Uses the evidence only for validation.

**Recommendation: A for the focused run, with B as a single pre-declared sensitivity scenario if pre-2005 AIDS is where A fails.** A is the simplest defensible option, and it turns the evidence into a declared assumption rather than a validation afterthought. No viral-suppression compartment is proposed.

**Remaining evidence gap:** a year-specific first-AIDS-in-care series with its entry-event definition, from SHM appendix tables or an earlier report edition. This is logged as a limitation, not a blocker.

## 5. Decision (Rishi, 2026-09-16): option B, two-era effective rate with a linear ramp

- **Definition:** an effective Chronic→AIDS annual probability for **all on-ART person-time**. No suppression state is added.
- **Time structure:** the early value applies up to 2000, ramps linearly over 2000–2010, and the recent value applies from 2010.
- **Priors:**
  - recent 0.002–0.008 (evidence-anchored, section 3);
  - early 0.002–0.030. This is an explicit **assumption**: the floor equals the recent floor, and the ceiling is twice the former box ceiling; no year-matched stored evidence exists.
- **No ordering constraint (early ≥ recent) is imposed.** SHM's declining first-AIDS curve would weakly support one, but it was not part of the decision; it is an open question for Rishi.

### Implementation

- `run_v3.py`: `_build_on_art_progression_schedule`.
  - Before the first anchor, the **first anchor value** is applied explicitly.
  - A scalar value next to a schedule requires `supersedes_value: true`, otherwise it raises.
  - Interpolation must be declared.
- Config `configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json`: both anchors equal the former scalar (0.0040).
  - A full stochastic run (seed 92000) gives **bitwise-identical outputs** to the original base case.
- Calibration space `configs/calib_space_v13_15_1975_orientation_seed_abc_onart_two_era_20260916.json`: 33 parameters (`chronic_to_aids_on_art_early`, `chronic_to_aids_on_art_recent`).
- Tests: `tests/test_on_art_two_era_progression_20260916.py` (10).
- The original configs and calibration space are unchanged.
