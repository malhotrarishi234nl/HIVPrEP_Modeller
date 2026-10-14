#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-overnight.txt

# These are zero-simulation contract tests for the new launch and binding path.
.venv/bin/python -m pytest -q \
  tests/test_overnight_matrix_harness.py \
  tests/test_hpc_scaling.py \
  tests/test_prepare_prep_policy_suite.py \
  tests/test_compare_coupled_calibration_suite.py \
  tests/test_coupled_calibration_search.py

.venv/bin/python tools/prepare_prep_policy_suite.py --validate-only
.venv/bin/python run_coupled_calibration.py \
  --validate-only \
  --plan outputs/suites/on_art_break_year_2008_vs_2010_20260928_rev6/cases/break_2008/resolved_configs/coupled_search_plan.json
.venv/bin/python run_coupled_calibration.py \
  --validate-only \
  --plan outputs/suites/on_art_break_year_2008_vs_2010_20260928_rev6/cases/break_2010/resolved_configs/coupled_search_plan.json

echo "Overnight VM environment and zero-simulation preflight passed."
