import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hta_config_loader import (
    compose_split_hta_config,
    economic_subset,
    load_hta_config,
)
from config_utils import validate_hta_config


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _path(*parts):
    return os.path.join(ROOT, *parts)


def test_split_config_preserves_legacy_economic_values():
    with open(_path("configs", "hta_config.json")) as f:
        legacy = json.load(f)

    split = load_hta_config(_path("configs", "hta_runtime_config.json"))

    assert economic_subset(split) == economic_subset(legacy)
    validate_hta_config(split)


def test_split_config_expands_declared_scenario_grid():
    split = load_hta_config(_path("configs", "hta_runtime_config.json"))
    names = {s["name"] for s in split["analysis_scenarios"]}

    assert "base_case_context" in names
    assert "scenario_a_lena_substitution__switch_10" in names
    assert "scenario_a_lena_substitution__switch_100" in names
    assert "scenario_b_lena_add_on__add_on_500" in names
    assert "scenario_c_oral_expansion_benchmark" in names

    primary = [s for s in split["analysis_scenarios"] if s.get("primary_comparison")]
    assert len(primary) == 5
    assert all(s["name"].startswith("scenario_a_lena_substitution") for s in primary)


def test_runtime_config_rejects_multiple_primary_scenario_families():
    with open(_path("configs", "hta_runtime_config.json")) as f:
        runtime = json.load(f)

    modified = copy.deepcopy(runtime)
    modified["scenarios"]["scenario_b_lena_add_on"]["primary_comparison"] = True

    with pytest.raises(ValueError, match="exactly one primary comparison"):
        compose_split_hta_config(
            modified,
            _path("configs", "hta_runtime_config.json"),
        )
