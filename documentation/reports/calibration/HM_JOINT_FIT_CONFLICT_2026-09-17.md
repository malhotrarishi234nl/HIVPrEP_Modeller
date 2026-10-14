> **REVISION 1 (2026-09-17) — read `outputs/calibration/hm_joint_fit_conflict_20260917_rev1/FINDINGS_REV1.md` first.**

> Archived under `documentation/reports/calibration/`; original filename retained.
> The rev-0 recommendation below is **withdrawn**. Three analysis errors were corrected: the factor-of-2 subgroup (1 near-level vector, not 7), the same-year onset/recognition "bound" (invalid, removed), and the regression-based "no useful experiment" claim (removed).
> Current conclusion: a substantial joint timing conflict is located in the sampled parameter sets; its cause is not identified well enough to justify a model change.
> SHM historical definition resolved: Dx 1996 is registration-conditioned, not like-for-like; 2000/2004 are close to like-for-like.
> Proposed next step: a 90-simulation controlled comparison (Part 1 diagnostic stock-flow balance at IDs 620/548; Part 2 2³ factorial at 620). Not launched; needs authorisation.
> Rev 1b: the accounting is diagnostic only; negative results don't rule out H2; the search trigger requires no deterioration at other anchors. Diagnosis tests now execute (38 pass); 1 deterministic-mode multiplier-neutrality failure is kept failing under a scoped launch-gate exception (tests/KNOWN_TEST_EXCEPTIONS.md). Runner run_joint_fit_comparison_20260917.py passed validate-only (0 simulations); launch awaits go-ahead.

# Joint diagnoses/AIDS fit-conflict investigation (TASK_BRIEF_JOINT_FIT_CONFLICT_2026-09-17)

**Status:** Phases A–B are complete, and so is Gate B. Phase C was not warranted, so no simulations were run and none were prepared.

**Outputs:** `outputs/calibration/hm_joint_fit_conflict_20260917/`

- Start with `FINDINGS.md`, then `EXISTING_EVIDENCE_DECISION.md`.
- Supporting evidence: `summary.json`, CSVs, `fig1`–`fig3`.

**Code**

- `hm_joint_fit_conflict.py`: pure helper functions.
- `tools/analyze_joint_fit_conflict_20260917.py`: read-only driver; refuses to write into a non-empty output directory.
- `tests/test_hm_joint_fit_conflict_20260917.py`: 15 tests.

**Recommendation:** a specific conflict needs a model/data decision.

- **What conflicts:** RIVM MSM AIDS in 1990 (318) against SHM MSM diagnoses in 1996–2004.
- **The trade-off:**
  - No saved vector fits both.
  - Vectors with the right early-AIDS level overshoot 1996–2004 diagnoses by at least ×8.9.
  - Vectors near the right diagnosis level undershoot 1990 AIDS by 69–94%.
  - The observation proxy cannot account for this.
- **Favoured reading, not proven:** MSM transmission inputs are calendar-time constant before 2019.
- **Decision required:** is calendar-time change in MSM transmission behaviour in scope for fitting the 15 joint anchors?

**Unchanged:** scoring contract, targets, bounds, discrepancy, NROY and simulator.

**Reproduce** (from `hiv-prep-model/`; delete or rename the output directory first):

```bash
set -o pipefail
mkdir -p outputs/calibration
./.venv/bin/python -u tools/analyze_joint_fit_conflict_20260917.py 2>&1 | tee outputs/calibration/hm_joint_fit_conflict_20260917.console.log
```
