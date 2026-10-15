# Pilot scientific-acceptance contract

Code-valid timing rows are not scientific approval. Production readiness now
requires three hash-linked artifacts around the pilot:

1. Before timing begins, complete
   `templates/pilot_vector_selection_protocol.json.template`. It records the
   exact full M0/M1 vectors and their ordered vector hashes, the scientific
   selection rationale, explicit authorization and reviewer/time, and exact
   plan, code, base-config, model-config and ordered parameter-space bindings.
2. The raw `robust_nroy_raw_pilot_measurements/1.2.0` artifact repeats that
   protocol path and SHA-256. Every row records valid UTC `started_utc` and
   `completed_utc`; authorization must strictly precede every timestamp.
3. After timing completes, complete
   `templates/pilot_scientific_approval.json.template`. It approves the exact
   raw-measurement SHA-256 and repeats the exact full timed vectors. Its
   reviewer/time must strictly follow every raw timestamp.

`robust_nroy_measured_pilot/2.1.0` hash-binds both companion artifacts and the
raw measurements. The priced-budget artifact already hash-binds the measured
pilot, so the full authority chain is sealed without adding another signed-plan
prerequisite. Missing artifacts, empty reviewers or rationale, naive/non-UTC
timestamps, inverted chronology, stale hashes, changed bindings, or any vector
difference fail production validation. The templates are deliberately
incomplete and are not authorization or evidence.
