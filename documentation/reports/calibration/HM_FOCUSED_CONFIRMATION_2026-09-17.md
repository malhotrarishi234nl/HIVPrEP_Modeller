# Confirmation of focused-run vectors 600 and 621

> Archived under `documentation/reports/calibration/`; original filename retained.

Purpose: distinguish reproducible predictive compatibility from poor mean trajectories
admitted by large between-seed variance. This is not another wave and cannot expand
or certify a calibrated region.

Run `run_focused_confirmation_20260917.py` from the repository. Default: 50 fresh
seeds 194000–194049 per vector, shared across vectors, 100 simulations total, 8 workers.
No simulator or history-matching scoring code was changed. Source artefacts, input
hashes and runtime compatibility are checked before execution. Source data remain
read-only; nonempty destination directories are refused. Partial raw output is
saved every ten simulations (checkpoint for inspection, not automatic resume).

Scoring preserves predictive local variance s(x)(1+1/K), with the existing seven
diagnosis and eight AIDS anchors. Reports diagnoses-only confirmation separately
from joint diagnosis–AIDS assessment at 10% and 20% discrepancy. Neither assessment
automatically starts wave 2. The 20% discrepancy remains sensitivity only.

Outputs in `outputs/calibration/hm_focused_confirmation_600_621_20260917`:

- `raw_replicates.csv`: each seed's annual outputs, retaining whole trajectories.
- `confirmed_means.csv`: means and empirical per-run variances.
- `confirmation_summary.csv` and `RUN_SUMMARY.md`: classifications and binding targets.
- `trajectory_diagnostics_*.csv`: per-target median, 5th/95th percentiles, SD and
  fraction within 20% of observed; also fraction of whole trajectories within that
  window at every anchor. These are descriptive windows, not new acceptance rules,
  confidence intervals or calibrated probabilities. Marginal intervals alone do
  not establish a jointly plausible trajectory.
- Per-target implausibility and variance-stability diagnostics.

Interpretation: passing predictive matching with poor means remains possible by
design. Inspect the raw trajectories and joint agreement before recommending local
search. Failure with fresh seeds does not prove that the surrounding region is empty.
Do not retune discrepancy or impose a post-hoc noise cutoff to rescue candidates.

Validation passed without simulations. Four focused unit tests passed, including
whole-trajectory agreement and the existing confirmation checks. Full-run execution
has not been performed here.

```bash
set -o pipefail
caffeinate -dims ./.venv/bin/python -u run_focused_confirmation_20260917.py --workers 8 \
  2>&1 | tee outputs/calibration/hm_focused_confirmation_600_621_20260917.console.log
```
