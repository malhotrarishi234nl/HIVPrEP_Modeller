from __future__ import annotations

import copy

import pandas as pd

from tools.probe_1970_burnin_v13_17 import (
    _markdown_table,
    default_scenarios,
    prepare_burnin_config,
)


def _minimal_config() -> dict:
    return {
        "run_control": {
            "start_year": {"value": 1975},
            "end_year": {"value": 2026},
            "deterministic_mode": {"value": False},
        },
        "output": {
            "enabled": {"value": True},
            "terminal_summary": {"value": True},
        },
        "population": {
            "total_size": {"value": 13_598_912},
            "age_sex_distribution": {"value": {"0-4": {"female": 0.02, "male": 0.02}}},
            "epidemic_seed": {
                "seed_year": {"value": 1975.0},
            },
        },
    }


def test_default_scenarios_include_1970_and_reference():
    names = [s["scenario"] for s in default_scenarios()]
    assert "start1975_seed1975_reference" in names
    assert "start1970_seed1970" in names
    assert "start1970_seed1972" in names


def test_prepare_burnin_config_does_not_mutate_input():
    cfg = _minimal_config()
    original = copy.deepcopy(cfg)

    result = prepare_burnin_config(cfg, start_year=1970, seed_year=1972.0)

    assert cfg == original
    assert result["run_control"]["start_year"]["value"] == 1970
    assert result["population"]["epidemic_seed"]["seed_year"]["value"] == 1972.0
    assert result["run_control"]["deterministic_mode"]["value"] is True
    assert result["output"]["enabled"]["value"] is False


def test_prepare_burnin_config_applies_embedded_fixed_then_override():
    cfg = _minimal_config()
    cfg["calibration_metadata"] = {
        "abc_smc_calibration": {
            "fixed_parameters": [
                {
                    "config_path": "run_control.start_year.value",
                    "value": 1975,
                },
                {
                    "config_path": "population.epidemic_seed.seed_year.value",
                    "value": 1975.0,
                },
            ]
        }
    }

    result = prepare_burnin_config(cfg, start_year=1972, seed_year=1972.0)

    assert result["run_control"]["start_year"]["value"] == 1972
    assert result["population"]["epidemic_seed"]["seed_year"]["value"] == 1972.0


def test_markdown_table_has_no_optional_dependency():
    df = pd.DataFrame([{"a": 1, "b": 2.34567}])
    text = _markdown_table(df)
    assert "| a | b |" in text
    assert "2.346" in text

