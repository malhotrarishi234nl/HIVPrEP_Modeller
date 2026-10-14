"""Declared warm-start (supplementary) design rows for a new sequence's first wave."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import hm_target_register as tr
import run_history_matching as rhm

ROOT = Path(__file__).resolve().parents[1]
SPACE = {"parameters": [{"name": "a", "min": 0.0, "max": 10.0}, {"name": "b", "min": 0.0, "max": 1.0}]}


def _write(tmp_path, frame):
    path = tmp_path / "ws.csv"
    frame.to_csv(path, index=False)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_loader_requires_matching_hash(tmp_path):
    path, digest = _write(tmp_path, pd.DataFrame({"a": [5.0], "b": [0.5]}))
    with pytest.raises(ValueError, match="SHA-256"):
        rhm.load_supplementary_design(SPACE, {"supplementary_design_file": str(path), "supplementary_design_sha256": "0" * 64})
    design, info = rhm.load_supplementary_design(SPACE, {"supplementary_design_file": str(path), "supplementary_design_sha256": digest})
    assert info["n_rows"] == 1 and list(design.columns[:2]) == ["a", "b"]


def test_loader_refuses_values_outside_the_prior_box(tmp_path):
    path, digest = _write(tmp_path, pd.DataFrame({"a": [11.0], "b": [0.5]}))
    with pytest.raises(ValueError, match="outside the prior box"):
        rhm.load_supplementary_design(SPACE, {"supplementary_design_file": str(path), "supplementary_design_sha256": digest})


def test_loader_refuses_missing_parameters(tmp_path):
    path, digest = _write(tmp_path, pd.DataFrame({"a": [1.0]}))
    with pytest.raises(ValueError, match="lacks parameters"):
        rhm.load_supplementary_design(SPACE, {"supplementary_design_file": str(path), "supplementary_design_sha256": digest})


def test_no_supplementary_setting_is_a_no_op():
    assert rhm.load_supplementary_design(SPACE, {}) == (None, None)


def test_real_focused_warm_start_file_validates_against_the_two_era_space():
    plan = json.loads((ROOT / "configs/hm_wave_plan_focused_diagnoses_then_aids_20260916.json").read_text())
    space = json.loads((ROOT / "configs/calib_space_v13_15_1975_orientation_seed_abc_onart_two_era_20260916.json").read_text())
    design, info = rhm.load_supplementary_design(space, plan["run_settings"])
    assert info["n_rows"] == 22 and len(design) == 22


WORKBOOK = (ROOT / "../data/raw/validation/DiseaseData.xlsx").resolve()


@pytest.mark.skipif(not WORKBOOK.exists(), reason="workbook unavailable")
def test_two_wave_staged_sequence_appends_warm_start_only_to_wave_one(tmp_path):
    register = tr.load_register(ROOT / "configs/hm_target_register_focused_20260916.json")
    assert {t.role for t in register.targets if t.series_name == "ecdc_hiv_confirmed_cases"} == {"cross_validation"}
    z = {t.key: t.z for t in register.targets}
    years = {"shm_new_diagnoses_msm": [2010], "rivm_aids_msm": [2010]}
    path, digest = _write(tmp_path, pd.DataFrame({"a": [5.0, 5.1], "b": [0.5, 0.4]}))
    rng = np.random.default_rng(0)

    def fake(design, base_config, calib_space, *, seed, workers=1, run_settings=None):
        f = design.copy()
        f.insert(0, "design_id", range(len(f)))
        for key, output in (("shm_new_diagnoses_msm@2010", "new_diagnoses_msm_2010"),
                            ("rivm_aids_msm@2010", "aids_first_diagnosis_proxy_msm_2010")):
            f[output] = z[key] * (1 + (f["a"] - 5.0) * 0.05)
            f["mcvar__" + output] = rng.uniform(1.0, 5.0, len(f))
        f["error"] = ""
        return f

    payload = rhm.run_waves(
        base_config={}, calib_space=SPACE, register=register,
        waves=[rhm.WaveSpec(index=1, n_design=60, seed=1, target_series=["shm_new_diagnoses_msm"]),
               rhm.WaveSpec(index=2, n_design=40, seed=2, target_series=["shm_new_diagnoses_msm", "rivm_aids_msm"])],
        stopping=rhm.StoppingRule(max_waves=2, min_volume_shrinkage=0.0, min_nroy_members=1),
        out_dir=tmp_path / "run", evaluate=fake, scored_years=years, disc_method="relative_to_target",
        run_settings={"replicates_per_design_point": 5, "v_ensemble_mode": "predictive_local",
                      "supplementary_design_file": str(path), "supplementary_design_sha256": digest},
    )
    h = payload["history"]
    assert h[0]["n_supplementary_design"] == 2 and h[0]["n_evaluated"] == 62
    assert len(h) == 2 and h[1]["n_supplementary_design"] == 0
    assert h[1]["n_targets"] == 2
    sources = pd.read_csv(tmp_path / "run/wave_01/design_sources.csv")
    assert (sources["source"] == "supplementary").sum() == 2
