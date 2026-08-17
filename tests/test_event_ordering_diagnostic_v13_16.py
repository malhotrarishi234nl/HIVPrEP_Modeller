"""
V13.16 diagnostic event-ordering tests.

The diagnostic flag must be opt-in. Missing/current preserves the production
ordering, while counterfactual variants should run without violating mass
balance and should expose audit columns.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from run_v3 import SimulationEngineV3


ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG_PATH = ROOT / "configs" / "v11_configuration.json"


def _base_config() -> dict:
    with BASE_CONFIG_PATH.open(encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["run_control"]["start_year"]["value"] = 1990
    cfg["run_control"]["end_year"]["value"] = 1992
    cfg["run_control"]["n_runs"]["value"] = 1
    cfg["run_control"]["deterministic_mode"]["value"] = True
    cfg["output"]["enabled"]["value"] = False
    cfg["output"]["terminal_summary"]["value"] = False
    return cfg


def _run_summary(cfg: dict, seed: int = 42) -> pd.DataFrame:
    engine = SimulationEngineV3(copy.deepcopy(cfg), seed=seed)
    return engine.run(run_number=1)["summary"]


def test_missing_diagnostic_event_order_flag_matches_explicit_current():
    base = _base_config()
    current = copy.deepcopy(base)
    current["run_control"]["diagnostic_event_order_variant"] = {"value": "current"}

    base_df = _run_summary(base)
    current_df = _run_summary(current)

    compare_cols = [
        "new_infections",
        "hiv_deaths",
        "aids_deaths",
        "new_diagnoses",
        "new_diagnoses_msm",
        "on_art_stock",
        "total_population",
    ]
    pd.testing.assert_frame_equal(
        base_df[compare_cols].reset_index(drop=True),
        current_df[compare_cols].reset_index(drop=True),
    )
    assert set(current_df["diagnostic_event_order_variant"]) == {"current"}


@pytest.mark.parametrize(
    "variant",
    [
        "mortality_before_diagnosis_art",
        "art_lag_after_diagnosis",
        "mortality_before_progression",
    ],
)
def test_diagnostic_event_order_variants_run_and_emit_audit_columns(variant: str):
    cfg = _base_config()
    cfg["run_control"]["diagnostic_event_order_variant"] = {"value": variant}

    df = _run_summary(cfg)

    assert not df.empty
    assert set(df["diagnostic_event_order_variant"]) == {variant}
    for column in [
        "aids_stock_start",
        "aids_off_art_start",
        "aids_undiagnosed_start",
        "aids_stock_pre_mortality",
        "aids_off_art_pre_mortality",
        "aids_undiagnosed_pre_mortality",
        "aids_art_initiations",
        "same_step_aids_art_rescue_proxy",
    ]:
        assert column in df.columns
        assert (df[column] >= 0).all()


def test_invalid_diagnostic_event_order_variant_raises():
    cfg = _base_config()
    cfg["run_control"]["diagnostic_event_order_variant"] = {"value": "not_a_variant"}
    with pytest.raises(ValueError, match="diagnostic_event_order_variant"):
        SimulationEngineV3(cfg, seed=42)

