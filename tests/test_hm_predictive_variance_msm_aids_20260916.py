"""Predictive V_ensemble contract, observed-AIDS proxy and RIVM candidate register (2026-09-16)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import aids_observation as ao
import hm_implausibility as hm
import hm_target_register as tr
import run_history_matching as rhm

ROOT = Path(__file__).resolve().parent.parent
REGISTER = ROOT / "configs" / "hm_target_register_rivm_msm_aids_20260916.json"
WORKBOOK = (ROOT / "../data/raw/validation/DiseaseData.xlsx").resolve()
needs_workbook = pytest.mark.skipif(not WORKBOOK.exists(), reason="disease workbook not available")


# ---------------------------------------------------------------- proxy
def test_proxy_sums_diagnosed_progression_and_diagnosis_in_aids_only():
    outputs = {
        "aids_progression_while_undiagnosed_msm": {1990: 100.0},
        "aids_progression_while_diagnosed_msm": {1990: 7.0, 1991: 2.0},
        "hiv_diagnoses_in_aids_stage_msm": {1990: 5.0, 1991: np.nan},
    }
    result = ao.add_observed_aids_proxy(outputs)
    assert result["aids_first_diagnosis_proxy_msm"] == {1990: 12.0}   # undiagnosed excluded; NaN year dropped
    assert "aids_first_diagnosis_proxy" not in result                 # missing total components: no series
    assert "aids_first_diagnosis_proxy_msm" not in outputs           # input not mutated


def test_proxy_never_fills_a_missing_component_year_with_zero():
    outputs = {
        "aids_progression_while_diagnosed": {2000: 3.0, 2001: 4.0},
        "hiv_diagnoses_in_aids_stage": {2000: 1.0},
    }
    assert ao.add_observed_aids_proxy(outputs)["aids_first_diagnosis_proxy"] == {2000: 4.0}


# ---------------------------------------------------------------- pointwise implausibility
def test_pointwise_implausibility_matches_hand_computation():
    matrix, names = hm.implausibility_matrix_pointwise(
        {"a": [10.0, 13.0, np.nan]}, {"a": 10.0}, {"a": [4.0, 1.0, np.nan]}, target_order=["a"]
    )
    assert names == ["a"]
    assert matrix[0, 0] == 0.0
    assert matrix[1, 0] == pytest.approx(3.0)
    assert np.isnan(matrix[2, 0])


def test_pointwise_implausibility_refuses_zero_variance_for_a_scorable_point():
    with pytest.raises(hm.VarianceBudgetError):
        hm.implausibility_matrix_pointwise({"a": [1.0]}, {"a": 0.0}, {"a": [0.0]}, target_order=["a"])


# ---------------------------------------------------------------- predictive variance
def _target(key="s@2000", output="out", z=100.0, role="calibration"):
    import hm_variance_budget as vb
    return tr.ScalarTarget(
        key=key, series_name=key.split("@")[0], model_output=output, year=2000, z=z,
        v_obs=vb.v_obs_underivable("test"), tier=1, role=role, source_sheet="t",
        source_organisation="t", z_derivation="t",
    )


def test_predictive_variance_is_s_times_one_plus_one_over_k():
    frame = pd.DataFrame({"out_2000": [100.0, 110.0], "mcvar__out_2000": [50.0, 8.0]})
    predictive, per_run, unresolved = rhm.pointwise_predictive_variance([_target()], frame, 10)
    assert unresolved == []
    np.testing.assert_allclose(per_run["s@2000"], [50.0, 8.0])
    np.testing.assert_allclose(predictive["s@2000"], [55.0, 8.8])


def test_predictive_variance_reports_missing_replicate_variance_instead_of_imputing():
    frame = pd.DataFrame({"out_2000": [1.0]})
    predictive, _, unresolved = rhm.pointwise_predictive_variance([_target()], frame, 10)
    assert predictive == {}
    assert unresolved and "mcvar__out_2000" in unresolved[0]["reason"]


def test_predictive_contract_is_wider_than_mean_matching_for_the_same_s():
    s, k = 40.0, 10
    assert s * (1 + 1 / k) > s / k


# ---------------------------------------------------------------- register
@needs_workbook
def test_register_loads_rivm_candidate_and_demotes_ecdc_aids():
    register = tr.load_register(REGISTER)
    roles = {t.series_name: t.role for t in register.targets}
    assert roles["rivm_aids_msm"] == "calibration"
    assert roles["rivm_aids_non_msm"] == "cross_validation"
    assert roles["rivm_aids_total"] == "cross_validation"
    assert roles["ecdc_aids_confirmed_cases"] == "cross_validation"
    by_key = {t.key: t for t in register.targets}
    msm, non_msm, total = (by_key[f"{name}@1990"] for name in ("rivm_aids_msm", "rivm_aids_non_msm", "rivm_aids_total"))
    assert msm.z == 318.0                                   # D76 Table 6.11, cross-checked in the design note
    assert msm.z + non_msm.z == total.z
    assert msm.model_output == "aids_first_diagnosis_proxy_msm"
    assert "rivm_aids_msm@2016" not in by_key               # held pending D79 reconciliation
    assert "rivm_aids_msm@2023" not in by_key               # explicitly incomplete
    assert msm.v_obs.status == "underivable"


@needs_workbook
def test_register_refuses_a_candidate_whose_hash_changed(tmp_path):
    spec = json.loads(REGISTER.read_text(encoding="utf-8"))
    for target in spec["targets"]:
        if target.get("source_type") == tr.RIVM_CANDIDATE_SOURCE_TYPE:
            target["candidate_sha256"] = "0" * 64
    spec["source_workbook"] = str(WORKBOOK)
    path = tmp_path / "register.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(tr.RegisterError, match="SHA-256"):
        tr.load_register(path, workbook_path=WORKBOOK)


# ---------------------------------------------------------------- scoring and loop
SPACE = {"parameters": [{"name": "a", "min": 0.0, "max": 10.0}]}


@needs_workbook
def test_scoring_contract_rejects_derived_outputs_under_predictive_local():
    register = tr.load_register(REGISTER)
    derived = [t for t in register.targets if t.model_output in tr.DERIVED_OUTPUTS]
    assert derived, "fixture expects the excluded derived non-MSM diagnosis series"
    fake = tr.TargetRegister(
        meta=register.meta,
        targets=[t.__class__(**{**t.__dict__, "role": "calibration"}) for t in derived],
    )
    waves = [rhm.WaveSpec(index=1, n_design=2, seed=1, target_series=[derived[0].series_name])]
    with pytest.raises(ValueError, match="derived outputs"):
        rhm.validate_scoring_contract(fake, waves, None, None, ensemble_mode=rhm.PREDICTIVE_LOCAL)


@needs_workbook
def test_wave_loop_under_predictive_local_writes_stability_diagnostic(tmp_path):
    register = tr.load_register(REGISTER)
    years = [1995, 2010]
    z = {y: next(t.z for t in register.targets if t.key == f"rivm_aids_msm@{y}") for y in years}
    rng = np.random.default_rng(3)

    def fake_evaluate(design, base_config, calib_space, *, seed, workers=1, run_settings=None):
        frame = design.copy()
        frame.insert(0, "design_id", range(len(frame)))
        for year in years:
            frame[f"aids_first_diagnosis_proxy_msm_{year}"] = z[year] + (frame["a"] - 5.0) * 20.0
            frame[f"mcvar__aids_first_diagnosis_proxy_msm_{year}"] = rng.uniform(20.0, 400.0, len(frame))
        frame["error"] = ""
        return frame

    payload = rhm.run_waves(
        base_config={}, calib_space=SPACE, register=register,
        waves=[rhm.WaveSpec(index=1, n_design=80, seed=1, target_series=["rivm_aids_msm"])],
        stopping=rhm.StoppingRule(max_waves=1, min_volume_shrinkage=0.1, min_nroy_members=1),
        out_dir=tmp_path / "run", evaluate=fake_evaluate,
        scored_years={"rivm_aids_msm": years}, disc_method="relative_to_target",
        run_settings={"replicates_per_design_point": 10, "v_ensemble_mode": "predictive_local"},
    )
    record = payload["history"][0]
    assert record["v_ensemble_mode"] == "predictive_local"
    stability = record["variance_contract_stability"]
    assert stability["n_nroy_s_lower_bound"] <= record["n_nroy"] <= stability["n_nroy_s_upper_bound"]
    assert stability["s_lower_factor"] < 1.0 < stability["s_upper_factor"]
    wave_dir = tmp_path / "run" / "wave_01"
    assert (wave_dir / "variance_contract_stability.json").exists()
    budget = pd.read_csv(wave_dir / "variance_budget.csv")
    assert budget["v_ensemble_derivation"].str.contains("POINTWISE").all()
    members = pd.read_csv(wave_dir / "nroy_members.csv")
    assert "variance_unstable" in members.columns


@needs_workbook
def test_predictive_local_primary_scoring_refuses_to_drop_a_target():
    register = tr.load_register(REGISTER)
    frame = pd.DataFrame({"design_id": [0, 1], "aids_first_diagnosis_proxy_msm_1995": [300.0, 320.0]})
    with pytest.raises(ValueError, match="could not resolve primary targets"):
        rhm.score_wave(
            frame, register, ["rivm_aids_msm"], scored_years={"rivm_aids_msm": [1995]},
            disc_method="relative_to_target", disc_multiplier=0.1,
            ensemble_mode=rhm.PREDICTIVE_LOCAL, replicates=10,
        )
