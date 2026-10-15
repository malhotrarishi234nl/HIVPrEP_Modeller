# Robust-NROY production-backbone reconciliation

**Status: CURRENT decision support as of 2026-10-15.** This report records a
read-only, no-engine audit. It does not select or sign a model configuration.

## Outcome

A documented 1980 candidate exists, but it is not a drop-in production base for
the current robust-NROY calibration space. The strongest historical evidence is
the preserved V13.1 prefinal joint rank-1 output. The current shared M0/M1
candidate instead descends from a config that explicitly labels its 1975
burn-in settings diagnostic and non-production.

The remaining choice is scientific, not mechanical:

- **Option A:** use the completed historical 1980 V13.1 prefinal rank-1 output
  as the backbone and approve or rebuild a calibration space compatible with
  that model generation; or
- **Option B:** retain the later V13.22 diagnosis and on-ART mechanisms, restore
  a scientifically justified 1980 base for them, and recalibrate rather than
  copying fields or changing the start year in place.

No owner-signed config-registry entry currently resolves this choice.

## Evidence inventory

### Current tracked proposal lineage

| Role | Path | SHA-256 | Relevant evidence |
|---|---|---|---|
| Direct source | `configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json` | `c7aaec0f3ae36929ff3266f7305c128952e08714575363436d273e73dbd53187` | Starts in 1975; deterministic; identifies itself as `13.14-diagnostic`; calibration metadata has `overall_accept=false`. |
| Prepared shared derivative | `configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_stochastic_m0_m1_20261015.json` | `fd50dd1a98965c9ea6b946c634060b0bd8ddac9334eb62ffc8d9cac67a63e7cf` | Mechanical derivative only: descriptive metadata, stochastic mode, and a complete disabled M1 block. |
| Current proposed M0 space | `configs/calib_space_v13_25_unordered_orientation_20260921.json` | `82970c6d542ea7319ba9cc807ea221bb43f4558afdc5d8abb37e96d649649a12` | Contains 33 parameters and identifies itself as a diagnostic space combining later model layers. |

The 1975 source states all of the following in its own metadata:

- production remains 1980;
- its population total and age-sex weights are the 1975 HMD values, while
  production uses the 1980 values;
- its epidemic seed year is diagnostic 1975 and the original production value
  is 1980; and
- period-specific 1975--1980 demographic inputs were not separately validated
  and are acceptable only for diagnostic burn-in testing.

The source-to-derivative audit found 14 scalar-leaf changes: descriptive
metadata, `run_control.deterministic_mode.value: true -> false`, and the added
disabled M1 block (`enabled=false`, `routine_hazard_scale=1.0`,
`aids_symptom_annual_probability=0.0`, `symptom_start_year=1985`, plus notes).
Existing source scientific values were not changed. This establishes a minimal
code-route derivative; it does not establish a suitable production backbone.

### Documented 1980 candidate

The strongest historical production-backbone evidence is preserved outside the
repository checkout at:

```text
/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /data/model-outputs/v11_outputs/calibration/lhs_pipeline_v131_prefinal/lhs_joint/branch_004/lhs_best_fit_rank_001.json
```

Its SHA-256 is
`f0f7efbcca6181fce76401a4604231b88927011b97eb736a5f0107a8848b9d02`.
It starts in 1980, ends in 2026, and its saved authoritative runtime flag is
stochastic. Its embedded LHS metadata records joint stage, rank 1, draw 17660,
creation on 2026-05-21, and ten rerank replicates.

The associated preserved status file is:

```text
/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /data/model-outputs/v11_outputs/calibration/lhs_pipeline_v131_prefinal/lhs_pipeline_status.json
```

Its SHA-256 is
`b7760d0f875e364807e51e46300d78371939b49fc9751bd52a4424acaa42a786`.
It records `status=completed` and names the original
`lhs_pipeline_v131_prefinal/lhs_joint/branch_004/lhs_best_fit_rank_001.json` as
`final_config`. `CHANGELOG.md` V13.4 independently records that the pipeline
selected branch 004, rank 1, and that `configs/model_final.json` was not the
direct LHS-selected candidate. The changelog also records the historical
deterministic/stochastic inconsistency; the preserved candidate's embedded
metadata says deterministic calibration, while its saved runtime flag is
stochastic.

Two tracked 1980 files do not supersede that evidence:

| Path | SHA-256 | Why it is not authority |
|---|---|---|
| `configs/1980_test.json` | `936dc7d1a50cc4107884715f62a0b83ee5af33afc925f1234387358658b9c1fe` | Its calibration status is "In Progress"; `configs/README.md` classifies it as testing and identity-gate support, not automatically the production base. |
| `configs/model_final.json` | `2aa27627379018d6b0f8dcd708e241fff24483d06b211ff2b1bac2b96e9b3540` | Its calibration status is also "In Progress"; `configs/README.md` classifies generic `model_final.json` names as ambiguous convenience names, and V13.4 says it was not the selected pipeline output. |

A later preserved 1980 recent-fit candidate also exists at
`/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /data/model-outputs/v11_outputs/calibration/lhs_pipeline_v13_5_recent_fit/lhs_joint/branch_004/lhs_best_fit_rank_002.json`
(SHA-256
`36f75cbaa4257130be4605498670edf2bad76a2439c045a7ba7ad0e430800e86`).
It is a distinct, deterministic lineage. The tracked thesis tooling describes
it as an interim screened handoff used for draft HTA, with final recalibration
and PSA still pending; it therefore does not silently replace the V13.1 rank-1
candidate.

## Scientific difference from the current 1975 source

A recursive scalar-leaf comparison between the preserved 1980 V13.1 rank-1
candidate and the current tracked 1975 V13.22 source found **461 changed
leaves**. Excluding descriptive metadata, notes, sources, and calibration
provenance left **179 science- or runtime-relevant changes**. This classification
is an audit aid, not an assertion that each of the 179 has independent
scientific importance.

The changes include the simulation start, total population, every initial
age-sex weight, seed year and later orientation-specific seed staging;
MSM-relative and exogenous non-MSM diagnosis structures; diagnosis schedule
shape and the 2019 anchor; two-era on-ART progression; and many fitted ART,
activity, contact, mixing, mortality, PrEP, diagnosis, and progression values.
Consequently, changing the 1975 source's year, population, and weights to 1980
would create an uncalibrated hybrid rather than recover the documented 1980
model.

## Compatibility with the current M0 space

The current 33-parameter M0 space cannot be injected unchanged into the
preserved V13.1 rank-1 config. After accounting for supported list-index paths
and the calibration runner's special orientation-stage-distribution injection,
eight ordinary parameter or fixed-value destinations are absent:

1. `transmission_model.disease.diagnosis.schedule.value.2019`
2. `transmission_model.disease.diagnosis.msm_relative_diagnosis.schedule.value.1996`
3. `transmission_model.disease.diagnosis.msm_relative_diagnosis.schedule.value.2004`
4. `transmission_model.disease.diagnosis.msm_relative_diagnosis.schedule.value.2019`
5. `transmission_model.disease.progression.chronic_to_aids_on_art.schedule.value.2000`
6. `transmission_model.disease.progression.chronic_to_aids_on_art.schedule.value.2010`
7. `transmission_model.disease.diagnosis.msm_relative_diagnosis.enabled.value`
8. `transmission_model.disease.diagnosis.msm_relative_diagnosis.schedule.interpolation.value`

The injection path is strict, so these are structural incompatibilities. A
read-only scan across tracked configs and preserved output/result/archive
trees enumerated **3,950 JSON candidates with a parsed 1980 start year**. None
contained every current direct and fixed destination. The closest later SNPE
candidates still lacked both on-ART 2000/2010 anchors and had no evidence that
promoted them to production authority. Therefore no identified 1980 file is a
safe drop-in for the current M0 space.

## Options and recommendation

### Option A — historical 1980 baseline

Select the preserved V13.1 prefinal branch-004 rank-1 output by exact hash.
A mechanically safe shared derivative could add only descriptive provenance
and the complete disabled M1 block; the file is already stochastic, so no mode
change is needed. Existing M0 scientific values would remain unchanged.

This option still requires an owner-approved calibration space compatible with
that model generation and an explicit statement that later V13.22 diagnosis
and on-ART mechanisms are outside the production model. It must not reuse the
current 33-dimensional space by silently dropping or inventing destinations.

### Option B — current mechanisms on a 1980 base

Retain the later diagnosis, on-ART progression, and related V13.22 mechanisms,
but establish their 1980 demographic and epidemic initialization scientifically
and recalibrate them. The preserved rank-1 config can be a historical comparator,
not a source from which later mechanism blocks are copied without validation.

### Recommendation and required decision

**Recommend Option B if robust-NROY is intended to make production claims using
the current V13.22 diagnosis and two-era on-ART mechanisms.** It is more work,
but it preserves the intended current model rather than creating an undocumented
hybrid. Option A is defensible only if the owner explicitly narrows the model
claim to the historical V13.1 backbone and approves a compatible calibration
design.

The owner must choose A or B, record the scope and rationale, and bind the exact
selected config and calibration-space hashes in the signed plan and the
authoritative config registry. Until then:

- do not sign `models.base_config`;
- do not alter the 1975 diagnostic source into a nominal 1980 file;
- do not promote `1980_test.json` or `model_final.json` by filename; and
- do not run the identity gate, pilot, or production engine.
