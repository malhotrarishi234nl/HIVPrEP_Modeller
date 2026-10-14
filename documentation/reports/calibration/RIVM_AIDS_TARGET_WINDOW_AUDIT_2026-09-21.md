# RIVM/SHM target-window audit

> Archived under `documentation/reports/calibration/`; original filename retained.

Date: 2026-09-21

Status: source-window decisions recorded below. No production target register or calibration configuration has yet been changed.

## Decisions recorded after review

The following source-window decisions were agreed on 2026-09-21:

- HIV diagnoses: observations from 1996 onward are eligible for primary calibration.
- AIDS diagnoses: observations from 2000 onward are eligible for primary calibration.
- AIDS observations before 2000, including MSM AIDS 1990 and 1995, remain visible as historical cross-validation/structural diagnostics and do not enter primary I_max.
- Explicitly incomplete terminal report years remain excluded from primary calibration until replaced by a mature vintage.

The agreed dates define source eligibility, not automatic inclusion of every annual value. The calibration will continue to use a predeclared set of anchor years so a densely reported series does not receive accidental extra weight.

### Tier and role are different fields

`Tier 1` means the observation is a direct surveillance or programme-register count rather than the output of another statistical model. `Primary calibration` means the observation is allowed to reject a candidate through the acceptance score. `Cross-validation` means the observation is reported against the model but does not reject a candidate.

A Tier-1 observation can therefore be cross-validation when it duplicates another target, uses a different surveillance system from the primary series, or lacks a validated mapping to the model output. Under the recorded decision, the selected HIV observations from 1996 onward and AIDS observations from 2000 onward are intended to be primary; the older Tier-1 AIDS observations are retained as cross-validation because of the surveillance-system seam and observation-mapping concern.

### Meaning of the target contract

The "target contract" is not a legal contract. It is the calibration specification frozen before a run: the included target series and years, which are primary versus cross-validation, their model-output mappings and uncertainty terms, the grouping/weighting rule, and the acceptance threshold. Freezing this specification before seeing a new run prevents post-hoc target dropping or weighting changes based on which candidates happen to fit.

### Recorded treatment of model discrepancy

The mature post-2000 AIDS anchors remain primary, with an explicit model-discrepancy allowance because `aids_first_diagnosis_proxy_msm` is an approximation to the registered SHM AIDS-diagnosis event.

The recommended predeclared specification is:

- primary: target-relative discrepancy sigma equal to 10% of the observed target (`fraction = 0.10`);
- structural sensitivity: target-relative discrepancy sigma equal to 20% (`fraction = 0.20`);
- acceptance cutoff: `I <= 3`, unchanged;
- the 20% sensitivity never promotes a candidate into the primary accepted set.

These fractions are standard-deviation terms, not direct pass bands. Ignoring other variance terms, 10% sigma combined with the cutoff of 3 gives an approximate +/-30% discrepancy envelope; 20% gives approximately +/-60%. The primary 10% value preserves the already declared baseline instead of tuning the allowance after observing which candidates pass. Results must report sensitivity to the allowance.

The confirmation diagnostics show why the mismatch must remain explicit: across the post-2000 target years, HIV diagnosis while already in AIDS contributes a median of about 42% of the current proxy, and replacing the proxy with the alternative "all biological progressions" count changes the annual value by a median of about 34% across the confirmed candidates and seeds. Those alternatives represent different event definitions, not repeated measurements of one event, so this spread is not used to estimate a Gaussian error term or to inflate the primary allowance. It is evidence that observation mapping remains a structural limitation.

## Question

Should the early RIVM MSM AIDS observations, especially 1990, remain primary calibration targets? If not, which historical windows are defensible and most relevant to forecasts through 2025 and beyond?

## Preliminary conclusion

Use different start rules for HIV diagnoses and AIDS diagnoses:

- HIV diagnoses: 1996 is a defensible earliest anchor for the SHM/ATHENA series. Treat pre-1996 HIV diagnosis counts as unsuitable for annual primary calibration because the historical SHM population is survivor-selected at the start of ATHENA.
- AIDS diagnoses: use 2000 onward for primary calibration. The 1996-1998 AIDS values still come from the older voluntary Health Inspectorate registry, not the later SHM monitoring system. The 1999 transition is source-ambiguous across report descriptions, so 2000 is the cleaner operational boundary.
- Retain pre-2000 AIDS values, including MSM AIDS 1990, as visible historical cross-validation/structural diagnostics. Do not use them in primary I_max unless a source-compatible observation model and uncertainty contract are established.

This recommendation does not rest on the model fitting 1990 poorly. It rests on source-system comparability and observation-model alignment.

## Evidence

### 1. The 1990 MSM count is mature but belongs to an older surveillance system

The value 318 MSM AIDS diagnoses in 1990 is reproduced unchanged in the RIVM 2003, 2008, 2009 and 2015/2016-era tables inspected in this audit. This stability argues against describing 318 as merely a late or unstable vintage.

However, RIVM states that AIDS cases before 1999 came from the Health Inspectorate AIDS registration, whereas later cases came from Stichting HIV Monitoring (SHM). The 2005 RIVM report provides the clearest procedural description: physicians voluntarily reported AIDS cases to the national registry between 1987 and 2002; the registry ended after the SHM HIV/AIDS monitoring system began; and the old registry was incomplete around 2000-2002.

Therefore the main concern is not arithmetic instability. It is a change in ascertainment system and uncertain comparability to the model's constructed AIDS-recognition proxy.

Primary sources:

- RIVM, *HIV and Sexually Transmitted Infections in the Netherlands in 2005*: https://www.rivm.nl/bibliotheek/rapporten/441100024.pdf
- RIVM, *HIV en AIDS in Nederland* (2003): https://www.rivm.nl/bibliotheek/rapporten/441100018.pdf
- RIVM, *Sexually transmitted infections, including HIV, in the Netherlands in 2008*: https://www.rivm.nl/bibliotheek/rapporten/210261005.pdf
- RIVM, *Sexually Transmitted Infections, including HIV in the Netherlands in 2016*: https://www.rivm.nl/bibliotheek/rapporten/2017-0003.pdf

### 2. A 1996 cutoff means different things for HIV and AIDS

RIVM reports that HIV cases diagnosed before 1996 in the historical SHM/ATHENA data include only people who survived until ATHENA began. That makes 1996 a meaningful lower boundary for annual HIV-diagnosis calibration.

It is not a clean boundary for AIDS diagnoses. AIDS observations for 1996-1998 remain part of the Health Inspectorate series. Later RIVM reports repeatedly identify SHM as the source from approximately 1999 onward, while the detailed 2005 description says trends after 2000 use HMF/SHM because of transition-period incompleteness. The conservative boundary is therefore 2000.

### 3. Recent terminal years are revised materially

The existing local vintage extraction shows strong revision of initially incomplete AIDS years:

- Total 2015 AIDS diagnoses: 127 in the 2016 publication, explicitly incomplete; 210 in 2017; 251 in 2023; 255 in 2024.
- MSM 2015: 53 in the incomplete first vintage; 104 in 2017; 124 in 2023; 125 in 2024.
- Total 2022: 83 in the incomplete 2023 publication; 138 in 2024.
- MSM 2022: 31 in the incomplete 2023 publication; 57 in 2024.

The present calibration targets use mature/latest-vetted values for the selected recent years (for example MSM 2015 = 125, 2019 = 83 and 2022 = 57). Explicitly incomplete terminal observations should remain excluded from primary calibration.

Local evidence: `outputs/calibration/rivm_aids_vintage_audit_20260916/REPORT.md` and `source_extraction.json`.

### 4. The model output is not yet a validated match to the surveillance event

The current `aids_first_diagnosis_proxy_msm` adds:

1. chronic-to-AIDS progression among people already diagnosed with HIV; and
2. HIV diagnosis among people already in the AIDS compartment.

RIVM tables describe AIDS patients by year of AIDS diagnosis. The audit has not established that the old voluntary registry or the later SHM system applies exactly the same recognition/reporting rule as this proxy. The proxy is additionally coupled to the diagnosis schedule through its second component. Reporting delay, incomplete notification, migrant history and prior reporting abroad are not represented.

That mismatch is sufficient reason to avoid using early AIDS observations as hard primary exclusions until the observation function is validated.

## Proposed primary windows

### Window A: historical treatment-era dynamics, 2000-2015

Use mature targets from the SHM era to assess whether the model reproduces:

- the diagnosis trajectory after combination ART became established;
- AIDS decline and subsequent level;
- changing diagnosis/ART initiation schedules; and
- the transition from epidemic history into the state that drives modern forecasts.

Candidate anchor years already present in the target system:

- MSM HIV diagnoses: 2000, 2004, 2010, 2015;
- MSM AIDS diagnoses: 2000, 2005, 2010, 2015.

### Window B: contemporary forecasting state, after 2015 through the latest mature observation

Use this window to constrain the current epidemic level and slope most relevant to projections:

- MSM HIV diagnoses: 2019 and 2024;
- MSM AIDS diagnoses: 2019 and 2022;
- retain 2015 as the seam/anchor between the two windows rather than counting it twice.

The most recent incomplete report year must not be treated as a mature target. A later forecast calibration may add newer years only after applying the same vintage-maturity rule.

### Historical diagnostics

- MSM HIV diagnosis 1996 may be retained as an early anchor or moved to cross-validation, depending on how strongly the calibration should emphasize the post-2000 forecasting state.
- MSM AIDS 1990 and 1995 should be cross-validation/structural diagnostics under the proposed policy.
- If retained diagnostically, report their signed residuals and explain the surveillance source seam and the model's missing duration-since-infection structure.

## Scoring implication

Do not simply drop two targets while retaining the current four-group objective. Re-freeze the target contract and define groups that match the scientific question. A defensible structure is:

1. MSM diagnoses, 2000-2015;
2. MSM AIDS diagnoses, 2000-2015;
3. MSM diagnoses, post-2015;
4. MSM AIDS diagnoses, post-2015.

Give the four groups declared equal group weight unless a forecasting-weight policy is justified in advance. Treat 2015 as a single seam observation, not as two independent targets. Keep 1996 diagnoses and pre-2000 AIDS in a separate reported cross-validation block.

## Work still required before another search

1. Encode the agreed eligibility rules: primary HIV diagnoses from 1996 onward, primary AIDS diagnoses from 2000 onward, and older AIDS observations as cross-validation.
2. Approve the two-window grouping and count the 2015 seam only once.
3. Document exactly how the post-2000 SHM AIDS-diagnosis event maps to the model proxy. The selected mature post-2000 AIDS anchors remain intended as primary, but any known mismatch must appear as an explicit model-discrepancy assumption rather than being ignored.
4. Encode the latest-vetted-vintage rule and exclude explicitly incomplete terminal years.
5. Freeze the resulting target specification before simulation, validate it mechanically, and only then launch a new calibration.

## Current recommendation

Use selected HIV-diagnosis anchors from 1996 onward and selected AIDS anchors from 2000 onward as primary evidence, emphasizing the predeclared 2000-2015 and post-2015 windows. Keep older AIDS data visible as cross-validation rather than calling it false or deleting it. Defer the MSM activity interaction until this target/observation specification is implemented and frozen.
