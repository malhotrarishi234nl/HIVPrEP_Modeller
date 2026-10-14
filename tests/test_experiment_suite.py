import csv
import json
from pathlib import Path

import pytest

import experiment_suite as suites


REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "configs/suites/examples/on_art_break_year_2008_vs_2010.json"


def test_example_validates_without_writing(tmp_path):
    before = set(tmp_path.iterdir())
    result = suites.validate_suite(EXAMPLE, REPO)
    assert result["ok"] is True
    assert result["simulations_started"] == 0
    assert set(tmp_path.iterdir()) == before


def test_prepare_materializes_linked_break_year_cases(tmp_path):
    output = tmp_path / "prepared_suite"
    manifest = suites.prepare_suite(EXAMPLE, output, REPO)
    assert manifest["simulations_started"] == 0
    assert manifest["launch_ready"] is False
    assert manifest["launch_blockers"]

    baseline_root = output / "cases/break_2010/resolved_configs"
    alternative_root = output / "cases/break_2008/resolved_configs"
    baseline = json.loads((baseline_root / "model_config.json").read_text())
    alternative = json.loads((alternative_root / "model_config.json").read_text())
    baseline_values = baseline["transmission_model"]["disease"]["progression"]["chronic_to_aids_on_art"]["schedule"]["value"]
    alternative_values = alternative["transmission_model"]["disease"]["progression"]["chronic_to_aids_on_art"]["schedule"]["value"]
    assert "2010" in baseline_values and "2008" not in baseline_values
    assert "2008" in alternative_values and "2010" not in alternative_values
    assert suites._sha256_file(baseline_root / "model_config.json") == suites._sha256_file(
        REPO / "configs/V13.22_20260604_nearfit_rank001_basecase_onart_two_era_20260916.json"
    )
    assert suites._sha256_file(baseline_root / "calib_space.json") == suites._sha256_file(
        REPO / "configs/calib_space_v13_25_unordered_orientation_20260921.json"
    )

    space = json.loads((alternative_root / "calib_space.json").read_text())
    assert space["parameters"][30]["config_path"].endswith(".2008")
    plan = json.loads((alternative_root / "coupled_search_plan.json").read_text())
    assert plan["inputs"]["base_config"].endswith("break_2008/resolved_configs/model_config.json")
    assert plan["inputs"]["expected_sha256"]["base_config"] == manifest["resolved_artifacts"]["break_2008"]["model_config"]["sha256"]

    with (output / "run_ledger.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    confirmation = [row for row in rows if row["case"] == "break_2008" and row["stage"] == "confirm"]
    assert len(confirmation) == 20
    assert {int(row["seed"]) for row in confirmation} == set(range(210100, 210120))
    baseline_plan = json.loads((baseline_root / "coupled_search_plan.json").read_text())
    assert baseline_plan["seeds"] == plan["seeds"]
    assert plan["seeds"]["comparison_group"] == "on_art_break_year_2008_vs_2010_crn_v1"
    assert (output / "commands.sh").stat().st_mode & 0o111
    commands = (output / "commands.sh").read_text()
    assert "suite is not launch-ready" in commands
    assert commands.index("exit 2") < commands.index("run_coupled_calibration.py")
    assert "compare_coupled_calibration_suite.py" in commands
    command_rows = json.loads((output / "commands.json").read_text())
    assert command_rows[-1]["case"] == "suite"
    assert command_rows[-1]["stage"] == "compare_cases"


def test_prepare_refuses_overwrite(tmp_path):
    output = tmp_path / "prepared_suite"
    output.mkdir()
    with pytest.raises(suites.SuiteError, match="refusing to overwrite"):
        suites.prepare_suite(EXAMPLE, output, REPO)
