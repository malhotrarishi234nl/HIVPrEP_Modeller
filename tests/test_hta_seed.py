import pytest

from run_scenarios import MultiScenarioRunner


def _runner_with_config(base_config):
    runner = MultiScenarioRunner.__new__(MultiScenarioRunner)
    runner.base_config = base_config
    return runner


def test_get_seed_reads_run_control_wrapped_value():
    runner = _runner_with_config({"run_control": {"seed": {"value": 8675309}}})

    assert runner._get_seed() == 8675309


def test_get_seed_reads_run_control_raw_integer():
    runner = _runner_with_config({"run_control": {"seed": 12345}})

    assert runner._get_seed() == 12345


def test_get_seed_requires_run_control_seed_even_if_legacy_seed_exists():
    runner = _runner_with_config(
        {"simulation_controls": {"seed": {"value": 42}}}
    )

    with pytest.raises(ValueError, match="run_control.seed"):
        runner._get_seed()


def test_get_seed_rejects_non_integer_value():
    runner = _runner_with_config({"run_control": {"seed": {"value": "abc"}}})

    with pytest.raises(ValueError, match="integer-compatible"):
        runner._get_seed()


def test_independent_scenario_seed_is_written_to_run_control():
    runner = _runner_with_config({})
    scenario_config = {"run_control": {"step_size": {"value": 3}}}

    runner._set_scenario_seed(scenario_config, 2026)

    assert scenario_config["run_control"]["step_size"] == {"value": 3}
    assert scenario_config["run_control"]["seed"] == {"value": 2026}


def test_independent_scenario_seed_creates_run_control_if_missing():
    runner = _runner_with_config({})
    scenario_config = {}

    runner._set_scenario_seed(scenario_config, 2027)

    assert scenario_config["run_control"]["seed"] == {"value": 2027}
