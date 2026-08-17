# Calibration Probe Scripts

Standalone, diagnostics-only runners moved out of the project root during the
2026-05-28 directory cleanup.

These scripts are not production entry points. They load existing candidate
configs, apply temporary in-memory perturbations, and write comparison outputs
under `outputs/`.

Run from the project root, for example:

```bash
python diagnostics/calibration_probes/run_historical_seed_diagnostics.py --help
```

Moved scripts:

- `audit_recent_fit_candidates.py`
- `run_calibration_joint_grid.py`
- `run_calibration_sensitivity_grid.py`
- `run_early_art_effectiveness_grid.py`
- `run_historical_seed_diagnostics.py`
- `run_seed_distribution_diagnostics.py`
- `run_seed_prevalence_grid_diagnostics.py`
- `run_seed_year_shift_diagnostics.py`
