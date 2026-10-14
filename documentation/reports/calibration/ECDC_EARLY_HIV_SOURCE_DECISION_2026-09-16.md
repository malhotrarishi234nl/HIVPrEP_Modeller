# ECDC HIV 1990–95: bounded source decision

> Archived under `documentation/reports/calibration/`; original filename retained.

**Question:** can `ecdc_hiv_confirmed_cases` 1990–95 be compared with the model's `new_diagnoses` (all diagnoses in the year)?
**Scope:** stored corpus and data extract only. This is decided independently of fit.

## Evidence

1. **Pre-1996 HIV diagnoses in the Dutch registration are survivor-restricted.** *Well-supported.*
   RIVM, *STI including HIV in the Netherlands in 2008* (`literature/van der Sande(2008) MAYBE.pdf`, §1.8, PDF p.24):
   - an anonymous HIV reporting system for patients entering care began only in January 2002 (SHM);
   - people diagnosed before SHM "were as far as possible included in the cohort retrospectively";
   - "The HIV cases diagnosed before 1996 only include persons who survived up to the start of the ATHENA project in 1996."
2. **The ECDC Netherlands HIV series appears to be that same registration.** *Moderately supported (inference, not a stated provenance).*
   - The stored ECDC extract (`ECDC_HIVAIDS_NL_20260514_150705.csv`) and SHM diagnoses (register) share the same year-to-year shape in the overlap: ECDC 668, 823, 667, 701, 871 against SHM 629, 752, 599, 646, 766 for 1996–2000, with a 1997 peak and 1998 dip in both.
   - ECDC runs about 6–14% higher, which is consistent with a different data vintage or inclusion rules.
   - No other national HIV case-reporting system existed before 2002 (item 1).
   - The corpus does not contain ECDC's own statement of source for these years.
3. **Consequence:** people diagnosed in 1990–95 who died before 1996 are absent from the observed counts. In the pre-HAART era that loss is large and largest for the earliest years. The observation is therefore a **one-sided (under-)count** of what the model's `new_diagnoses` measures. The 2026-09-15 arithmetic audit of this series was correct but did not test this construct. *Well-supported* given items 1–2.
4. **Related, noted only:** RIVM AIDS counts up to 1999 come from the **voluntary** Inspectorate AIDS registry (same source, same page). Completeness is not quantified. This affects the early `rivm_aids_msm` anchors (1990, 1995) in the same direction, a possible undercount, but it is not a survivor restriction. It is *Weakly supported* as a concern and does not change their use here.

## What removing them leaves

- SHM MSM diagnoses start at 1996.
- Before 1996 the epidemic would be constrained only by RIVM MSM AIDS 1990 and 1995. That registry is not survivor-restricted.
- The 1990s would therefore still be constrained, but more weakly.

## Options

- **1. Hold out as cross-validation, reported as one-sided.** Remove the six anchors from the primary set. Report whether the model's diagnoses are **at least** the observed counts. No code change. *(Recommended: simplest and matches the construct.)*
- **2. Keep as a one-sided calibration constraint.** Implausibility counts only when model < observed: I = max(0, z − f)/σ. This retains the information honestly, but needs a small runner change and tests, and σ has no stated basis for a lower bound.
- **3. Model the survivor-restricted observation.** Count 1990–95 diagnoses surviving to 1996. This needs cohort tracking by diagnosis year: a real mechanism addition, not justified at this stage.
