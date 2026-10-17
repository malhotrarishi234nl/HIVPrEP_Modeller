"""Evidence register and economic-recipe persistence used by the workbench UI."""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench_ui.economics import (
    EconomicWorkbenchError,
    list_economic_recipes,
    save_economic_recipe,
)
from workbench_ui.register import (
    build_register,
    cost_matrix,
    distribution_curve,
    evidence_summary,
)

ROOT = Path(__file__).resolve().parents[1]


def test_register_covers_model_and_costing_with_evidence_columns():
    frame = build_register(ROOT)
    summary = evidence_summary(frame)
    assert summary["parameters"] > 50
    assert {"Parameter", "Value", "95% interval", "Source", "Config path"} <= set(frame.columns)
    origins = {path.split("::", 1)[0] for path in frame["Config path"]}
    assert origins == {"model", "costing"}


def test_interval_derived_from_declared_distribution():
    frame = build_register(ROOT)
    row = frame[frame["Config path"].str.endswith("beta_per_route.anal_receptive.chronic")].iloc[0]
    assert row["Interval basis"] == "From distribution"
    low, high = (float(part) for part in row["95% interval"].split("–"))
    assert low < float(row["Value"]) < high


def test_unparameterised_beta_is_not_given_a_fake_interval():
    frame = build_register(ROOT)
    placeholders = frame[frame["Distribution"].str.contains("placeholder")]
    assert not placeholders.empty
    assert not (placeholders["Interval basis"] == "From distribution").any()


def test_distribution_curve_only_for_parameterised_distributions():
    assert distribution_curve(None) is None
    assert distribution_curve({"type": "fixed"}) is None
    assert distribution_curve({"type": "beta", "alpha": 0, "beta": 0}) is None
    curve, (low, high) = distribution_curve({"type": "gamma", "mean": 100.0, "se": 10.0})
    assert low < 100 < high and len(curve) > 10


def test_cost_matrix_lists_units_with_sources():
    matrix = cost_matrix(ROOT)
    assert not matrix.empty
    assert {"Group", "Item", "Value (EUR)", "Source"} <= set(matrix.columns)


def test_economic_recipe_round_trip_and_no_overwrite(tmp_path):
    settings = {"scenario_ids": ["a"], "costing_overrides": {"x.value": 1.5}, "runs": 2}
    path = save_economic_recipe(tmp_path, "My forecast!", settings)
    assert path.name == "economic-my-forecast.json"
    [recipe] = list_economic_recipes(tmp_path)
    assert recipe["name"] == "My forecast!" and recipe["settings"] == settings
    with pytest.raises(EconomicWorkbenchError):
        save_economic_recipe(tmp_path, "my forecast", settings)
    with pytest.raises(EconomicWorkbenchError):
        save_economic_recipe(tmp_path, "!!!", settings)
