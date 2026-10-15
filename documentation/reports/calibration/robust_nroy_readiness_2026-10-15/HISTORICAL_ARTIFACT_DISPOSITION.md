# Historical artifact disposition

After external-input reconciliation, the guarded repository-wide rerun has 29
failures and no setup errors. Every remaining failure is a legitimate
fail-closed hash check over two historical scientific lineages. Resealing is
prohibited because it would attribute old results to current code.

Aggregate hashes below are SHA-256 hashes of the sorted recorded module-hash
maps, not new seals over the scientific results.

| Artifact path | Recorded code-map hash | Current code-map hash / mismatch | Consequence | Recommended disposition |
|---|---|---|---|---|
| `outputs/calibration/coupled_forecast_windows_20260921_search` | `dcf0b4f065ebb1aab9f831673024e8edd56376e31cc4e53286f02c8d84beafca` (manifest `d1dcac90…600a7`) | `5b03ba70b9f24bb139f66984f06414750c10f151252d2c8681cbbc599f7c6d7e`; changed `aids_observation.py`, `calib_extractor.py`, `config_utils.py`, `run_coupled_calibration.py`, `run_lhs_calibration.py`, `run_v3.py` | Cannot be loaded as a current search or parent | **(a) Retain as historical evidence** and **(c) retire from current readiness claims**. Rerun only after explicit authorization and a signed current design. |
| `outputs/calibration/coupled_forecast_windows_20260921_confirmation` | Inherits the search lineage above; manifest `d5195301…3c757` | Same six-module mismatch | Cannot establish current confirmed anchors | **(a)+(c)**; do not relabel as current confirmation. |
| `outputs/calibration/forecast_readiness_confirmation_20260921` | Inherits `dcf0b4f0…beafca` | Same six-module mismatch | Four read-only validation tests fail before selection evidence is consumed | **(a)+(c)**. |
| `outputs/calibration/art_interpolation_shape_audit_20260921` | Inherits `dcf0b4f0…beafca` | Same six-module mismatch | Six audit tests cannot claim current-code geometry | **(a)+(c)**. |
| `outputs/calibration/msm_beta_response_audit_20260921` | Inherits `dcf0b4f0…beafca` | Same six-module mismatch | Three audit tests cannot claim current-code response | **(a)+(c)**. |
| `outputs/calibration/post2015_stock_flow_decomposition_20260921` | Inherits `dcf0b4f0…beafca` | Same six-module mismatch | Read-only validator refuses current interpretation | **(a)+(c)**. |
| `outputs/calibration/post2019_diagnosis_disruption_audit_20260921` | Inherits `dcf0b4f0…beafca` | Same six-module mismatch | Two audit tests cannot claim current-code diagnosis behavior | **(a)+(c)**. |
| `outputs/calibration/recent_mechanism_audit_20260921` | Inherits `dcf0b4f0…beafca` | Same six-module mismatch | Four tests refuse current mechanism claims | **(a)+(c)**. |
| `outputs/calibration/temporal_diagnosis_shape_audit_20260921` | Inherits `dcf0b4f0…beafca` | Same six-module mismatch | Seven tests cannot claim current-code temporal geometry | **(a)+(c)**. |
| `outputs/calibration/hm_focused_diagnoses_then_aids_20260916` | `864bb733cf6b893e30a8e465d922de4a7c9d1e2643536bf02f4f6ce643bcdc5d` (manifest `667f844e…abab`) | `d2e231efc4d9c06886ec45f42352c62289f1acd43247da68743dce2fb96d169a`; changed `aids_observation.py`, `calib_extractor.py`, `config_utils.py`, `run_lhs_calibration.py`, `run_v3.py` | Cannot be treated as a current history-matching source | **(a)+(c)**. |
| `outputs/calibration/historical_transmission_prepared_20260917` | Inherits `864bb733…cdc5d` | Same five-module mismatch | Real-input preparation test correctly refuses attribution | **(a)+(c)**. |
| `outputs/calibration/hm_joint_fit_comparison_20260917` | Inherits `864bb733…cdc5d` | Same five-module mismatch | Validate-only comparison returns fail-closed | **(a)+(c)**. |

## Decision boundary

No artifact needs to be deleted. A rerun under current code is scientifically
legitimate only if the analysis is still needed and the user authorizes the
engine work. Until then the artifacts remain useful historical records but are
not evidence of current production readiness.
