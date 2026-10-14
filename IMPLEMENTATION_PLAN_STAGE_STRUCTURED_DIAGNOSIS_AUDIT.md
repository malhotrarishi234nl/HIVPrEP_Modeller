# Stage-structured diagnosis audit: implementation plan

Date: 2026-09-22

1. Add a disabled-by-default `disease.diagnosis.stage_structured_diagnosis`
   configuration block reader to the production engine. Validate `enabled`,
   `routine_hazard_scale`, and `aids_symptom_annual_probability` strictly. Keep
   the legacy diagnosis path byte-for-byte active when disabled and route the
   enabled identity profile (`rho=1`, `p_symptom_aids=0`) through that same path
   so its draw keys and counter-based random streams are identical.
2. For enabled non-identity profiles, evaluate the already-injected base
   diagnosis schedule and MSM-relative schedule at the current calendar time,
   validate their effective annual probabilities, convert them to hazards, add
   the common AIDS-only symptom hazard, and draw diagnoses by population and
   stage. Do not change C-1 exogenous diagnoses or any other transition.
3. Extend the passive diagnosis observation counters to Acute, Chronic, and
   AIDS for total, MSM, and non-MSM. Route them through existing timestep and
   annual output machinery and assert their sums equal endogenous diagnoses.
4. Add a frozen audit plan containing candidates 1326 and 1788, the twelve
   declared profiles, seeds 200600--200609, source-manifest hashes, and the
   240-trajectory budget.
5. Add a resumable, no-overwrite audit runner. Validation will load and hash the
   injected candidate configurations without constructing engines; profile
   application may alter only the new diagnosis block. Each trajectory will
   capture annual outputs, stage/activity stocks, diagnosis/exogenous splits,
   diagnosis/ART mortality-state information available from the state ledger,
   2024 year-end and 2025 start snapshots, and the exact post-2015 MSM ledger.
6. Score every candidate/profile against the unchanged forecast-window target
   contract at 10% and 20% discrepancy, write target residuals and the four
   contract group summaries, compute paired seed changes from the identity
   baseline, apply the predeclared promising gates, and produce at most four
   decision figures plus the required report and manifest.
7. Add focused automated tests for disabled and enabled-identity equivalence,
   hazard arithmetic, group/stage isolation, invalid-input failure, passive
   counter identities, C-1 invariance, profile isolation/budget, validate-only
   purity, no-overwrite behavior, and exact ledger closure. Run the relevant
   existing diagnosis, AIDS, RNG, temporal-audit, stock-flow, and coupled-search
   tests before the smoke and frozen run.

The production baseline and HTA configurations will not be edited.
