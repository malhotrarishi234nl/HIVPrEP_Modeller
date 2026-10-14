"""Tests for the 2026-09-17 joint fit-conflict diagnostic helpers (hm_joint_fit_conflict.py).

Brief test list items 6 (perturbation ranges) and the Phase C runner parts of 9/10 are NOT
APPLICABLE: Gate B concluded no simulation experiment is warranted, so no runner exists.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import hm_joint_fit_conflict as jf

ROOT = Path(__file__).resolve().parents[1]


def _anchors():
    return [
        jf.Anchor("shm_new_diagnoses_msm@2000", jf.DIAGNOSIS_SERIES, "diagnosis", "new_diagnoses_msm_2000", 360.0, 0.0),
        jf.Anchor("rivm_aids_msm@1990", jf.AIDS_SERIES, "aids", "aids_first_diagnosis_proxy_msm_1990", 318.0, 4.0),
    ]


def _frame(values_by_id):
    """values_by_id: {id: {'dx': [...], 'aids': [...]}} -> moment frame like evaluated.csv."""
    rows = []
    for vid, v in values_by_id.items():
        dx, aids = np.asarray(v["dx"], float), np.asarray(v["aids"], float)
        rows.append({"design_id": vid,
                     "new_diagnoses_msm_2000": dx.mean(), "mcvar__new_diagnoses_msm_2000": dx.var(ddof=1),
                     "aids_first_diagnosis_proxy_msm_1990": aids.mean(),
                     "mcvar__aids_first_diagnosis_proxy_msm_1990": aids.var(ddof=1)})
    return pd.DataFrame(rows)


# 1. hand-computed residuals, variance, SE, denominator, Imax -------------------------------

def test_hand_computed_residuals_and_imax():
    frame = _frame({7: {"dx": [400, 420, 380, 440, 360], "aids": [300, 310, 290, 320, 280]}})
    long = jf.residual_table(frame, _anchors(), replicates=5, discrepancy=0.1)
    dx = long[long.target_key == "shm_new_diagnoses_msm@2000"].iloc[0]
    # mean 400; deviations 0,20,-20,40,-40 -> SS 4000; s2 = 4000/4 = 1000 (variance, not SD)
    assert dx.sim_mean == pytest.approx(400.0)
    assert dx.sample_variance_s2 == pytest.approx(1000.0)
    assert dx.sample_sd == pytest.approx(np.sqrt(1000.0))
    assert dx.mc_se_of_mean == pytest.approx(np.sqrt(1000.0 / 5))
    assert dx.v_ensemble == pytest.approx(1000.0 + 200.0)
    assert dx.v_disc == pytest.approx(36.0 ** 2)
    total = 1200.0 + 1296.0
    assert dx.total_variance == pytest.approx(total)
    assert dx.implausibility == pytest.approx(40.0 / np.sqrt(total))
    assert dx.signed_rel_error == pytest.approx(40.0 / 360.0)
    assert dx.frac_v_process + dx.frac_v_mc + dx.frac_v_disc + dx.frac_v_obs == pytest.approx(1.0)
    aids = long[long.target_key == "rivm_aids_msm@1990"].iloc[0]
    # mean 300, s2 = (0+100+100+400+400)/4 = 250; V_obs 4 included
    assert aids.total_variance == pytest.approx(4.0 + 250.0 * 1.2 + 31.8 ** 2)
    summary = jf.vector_summary(long)
    assert summary.imax_joint.iloc[0] == pytest.approx(max(dx.implausibility, aids.implausibility))


def test_sd_is_not_used_as_variance():
    frame = _frame({1: {"dx": [0, 20, 40, 60, 80], "aids": [318] * 4 + [319]}})
    long = jf.residual_table(frame, _anchors(), replicates=5)
    row = long[long.block == "diagnosis"].iloc[0]
    assert row.v_process_s2 == pytest.approx(1000.0)  # s2 of 0..80 step 20 is 1000, SD 31.6
    assert row.v_process_s2 != pytest.approx(row.sample_sd)


# 2. joins -----------------------------------------------------------------------------------

def test_join_is_row_order_independent_and_rejects_duplicates():
    left = pd.DataFrame({"design_id": [1, 2, 3], "a": [10, 20, 30]})
    right = pd.DataFrame({"design_id": [3, 1, 2], "b": [300, 100, 200]})
    joined = jf.join_on_id(left, right.sample(frac=1, random_state=4), "design_id", "t")
    assert joined.set_index("design_id").loc[3, "b"] == 300
    with pytest.raises(jf.DataContractError):
        jf.join_on_id(left, pd.concat([right, right.iloc[[0]]]), "design_id", "dup")
    with pytest.raises(jf.DataContractError):
        jf.join_on_id(left, right[right.design_id != 2], "design_id", "unmatched")


def test_duplicate_seed_rows_rejected():
    raw = pd.DataFrame({"source_design_id": [1, 1], "confirmation_seed": [5, 5], "x": [1.0, 2.0]})
    with pytest.raises(jf.DataContractError):
        jf.paired_seed_differences(raw, ["x"], id_a=1, id_b=1)


# 3. fail closed ------------------------------------------------------------------------------

def test_missing_or_nonfinite_target_fails_closed():
    frame = _frame({1: {"dx": [1, 2, 3], "aids": [4, 5, 6]}})
    with pytest.raises(jf.DataContractError):
        jf.residual_table(frame.drop(columns=["mcvar__aids_first_diagnosis_proxy_msm_1990"]), _anchors(), replicates=3)
    bad = frame.copy()
    bad.loc[0, "new_diagnoses_msm_2000"] = np.nan
    with pytest.raises(jf.DataContractError):
        jf.residual_table(bad, _anchors(), replicates=3)
    with pytest.raises(jf.DataContractError):
        jf.residual_table(frame, _anchors(), replicates=1)


def test_nonpositive_target_flagged_not_divided():
    frame = _frame({1: {"dx": [1, 2, 3], "aids": [4, 5, 6]}})
    anchors = [jf.Anchor("x@1", jf.DIAGNOSIS_SERIES, "diagnosis", "new_diagnoses_msm_2000", 0.0, 1.0), _anchors()[1]]
    long = jf.residual_table(frame, anchors, replicates=3)
    row = long[long.target_key == "x@1"].iloc[0]
    assert bool(row.target_nonpositive) and np.isnan(row.signed_rel_error)


# 4. Pareto -------------------------------------------------------------------------------------

def test_pareto_known_fixture_and_source_labels():
    x = [1, 2, 3, 2, 5, np.nan]
    y = [5, 2, 1, 3, 5, 0]
    assert jf.pareto_mask(x, y).tolist() == [True, True, True, False, False, False]
    summary = pd.DataFrame({"design_id": range(6), "source": ["lhs", "supplementary"] * 3,
                            "max_abs_rel_error_diagnosis": x, "max_abs_rel_error_aids": y})
    summary["pareto"] = jf.pareto_mask(summary.max_abs_rel_error_diagnosis, summary.max_abs_rel_error_aids)
    assert summary.loc[summary.pareto, "source"].tolist() == ["lhs", "supplementary", "lhs"]


# 5. bootstrap of whole trajectories ----------------------------------------------------------

def _raw_two_vectors(seeds=range(100, 110)):
    rng = np.random.default_rng(0)
    rows = []
    for vid in (600, 621):
        for s in seeds:
            base = rng.normal(0, 1)
            rows.append({"source_design_id": vid, "confirmation_seed": s,
                         "new_diagnoses_msm_2000": 360 + 50 * base + vid,
                         "aids_first_diagnosis_proxy_msm_1990": 318 + 1000 * s})  # year value encodes seed
    return pd.DataFrame(rows)


def test_bootstrap_resamples_whole_rows_and_is_deterministic():
    raw = _raw_two_vectors()
    anchors = _anchors()
    a = jf.bootstrap_whole_trajectories(raw, anchors, vector_ids=[600, 621], k_values=[5], n_boot=20, seed=11)
    b = jf.bootstrap_whole_trajectories(raw.sample(frac=1, random_state=1), anchors, vector_ids=[600, 621],
                                        k_values=[5], n_boot=20, seed=11)
    pd.testing.assert_frame_equal(a, b)
    # Whole-row resampling: with K=2 and identical draws for both vectors, the AIDS column (which encodes the
    # seed) must give identical implausibility for both vectors in every draw (paired, same seeds).
    c = jf.bootstrap_whole_trajectories(raw, anchors, vector_ids=[600, 621], k_values=[2], n_boot=50, seed=3)
    w = c.pivot(index="draw", columns="source_design_id", values="imax_aids")
    assert np.allclose(w[600], w[621])


def test_bootstrap_rejects_unpaired_seed_sets():
    raw = _raw_two_vectors()
    raw = raw[~((raw.source_design_id == 621) & (raw.confirmation_seed == 100))]
    with pytest.raises(jf.DataContractError):
        jf.bootstrap_whole_trajectories(raw, _anchors(), vector_ids=[600, 621], k_values=[5], n_boot=2, seed=1)


# 7. paired differences align on seed ------------------------------------------------------------

def test_paired_differences_align_shuffled_rows():
    raw = _raw_two_vectors()
    d1 = jf.paired_seed_differences(raw, ["new_diagnoses_msm_2000"], id_a=600, id_b=621)
    d2 = jf.paired_seed_differences(raw.sample(frac=1, random_state=9), ["new_diagnoses_msm_2000"], id_a=600, id_b=621)
    pd.testing.assert_frame_equal(d1, d2)
    a = raw[raw.source_design_id == 600].set_index("confirmation_seed").new_diagnoses_msm_2000
    b = raw[raw.source_design_id == 621].set_index("confirmation_seed").new_diagnoses_msm_2000
    assert np.allclose(d1.set_index("confirmation_seed").new_diagnoses_msm_2000, (b - a).sort_index())


# 8. primary scoring unchanged by diagnostic options ------------------------------------------------

def test_residual_table_matches_score_wave_and_sensitivity_does_not_touch_primary():
    import hm_target_register as tr
    import run_history_matching as rhm

    source = ROOT / "outputs/calibration/hm_focused_diagnoses_then_aids_20260916"
    if not (source / "wave_01/evaluated.csv").exists():
        pytest.skip("source run not present")
    manifest = json.loads((source / "manifest.json").read_text())
    register = tr.load_register(ROOT / "configs/hm_target_register_focused_20260916.json")
    evaluated = pd.read_csv(source / "wave_01/evaluated.csv")
    series = [jf.DIAGNOSIS_SERIES, jf.AIDS_SERIES]
    kwargs = dict(disc_method="relative_to_target", ensemble_mode="predictive_local", replicates=5,
                  scored_years=manifest["scored_years"])
    before = rhm.score_wave(evaluated, register, series, disc_multiplier=0.1, **kwargs)
    targets = register.restrict_years([t for t in register.calibration_targets() if t.series_name in series],
                                      manifest["scored_years"])
    anchors = jf.anchors_from_register(targets)
    jf.residual_table(evaluated, anchors, replicates=5, discrepancy=0.2)  # sensitivity computed first
    long = jf.residual_table(evaluated, anchors, replicates=5, discrepancy=0.1)
    wide = long.pivot(index="design_id", columns="target_key", values="implausibility")[[t.key for t in before["targets"]]]
    assert np.abs(wide.to_numpy() - before["matrix"]).max() < 1e-9
    after = rhm.score_wave(evaluated, register, series, disc_multiplier=0.1, **kwargs)
    assert np.array_equal(before["combined"].i_max, after["combined"].i_max)
    # wave-1 diagnoses-only primary survivors reproduce exactly
    diag = rhm.score_wave(evaluated, register, [jf.DIAGNOSIS_SERIES], disc_multiplier=0.1, **kwargs)
    assert sorted(evaluated.design_id[diag["mask"]].tolist()) == [600, 621]


# 9. output directory protection (analysis script; runner N/A) ------------------------------------

def test_analysis_refuses_nonempty_output(tmp_path):
    (tmp_path / "existing.txt").write_text("keep")
    result = subprocess.run([sys.executable, str(ROOT / "tools/analyze_joint_fit_conflict_20260917.py"),
                             "--output-dir", str(tmp_path)], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode != 0 and "REFUSING" in (result.stderr + result.stdout)
    assert (tmp_path / "existing.txt").read_text() == "keep"


# 10. provenance mismatch blocks use --------------------------------------------------------------

def test_tampered_artefact_blocks_verification(tmp_path):
    from run_hm_confirmation import _sha256, _verified_manifest

    run = tmp_path / "run"
    run.mkdir()
    (run / "evaluated.csv").write_text("design_id\n0\n")
    (run / "manifest.json").write_text(json.dumps({"artefacts": [
        {"path": "evaluated.csv", "bytes": (run / "evaluated.csv").stat().st_size, "sha256": _sha256(run / "evaluated.csv")}]}))
    _verified_manifest(run.resolve())
    (run / "evaluated.csv").write_text("design_id\n1\n")
    with pytest.raises(ValueError):
        _verified_manifest(run.resolve())


def test_mechanism_map_covers_all_33_parameters_and_rejects_unknown():
    space = json.loads((ROOT / "configs/calib_space_v13_15_1975_orientation_seed_abc_onart_two_era_20260916.json").read_text())
    names = [p["name"] for p in space["parameters"]]
    table = jf.mechanism_table(names)
    assert len(table) == 33 and table.mechanism_group.notna().all()
    with pytest.raises(jf.DataContractError):
        jf.mechanism_table(names + ["not_a_parameter"])


def test_leave_out_rescore_is_explanatory_and_counts():
    long = pd.DataFrame({"design_id": [1, 1, 2, 2], "target_key": ["a", "b", "a", "b"], "implausibility": [5.0, 1.0, 2.0, 4.0]})
    out = jf.leave_out_rescore(long, {"none": [], "drop_a": ["a"]}).set_index("dropped")
    assert out.loc["none", "n_vectors_imax_le_3"] == 0
    assert out.loc["drop_a", "n_vectors_imax_le_3"] == 1 and out.loc["drop_a", "argmin_id"] == 1


# Rev 1: multiplicative window -------------------------------------------------------------------

def test_within_factor_is_multiplicative_not_abs_relative_error():
    sim = np.array([0.0, 100.0, 180.0, 199.9, 200.0, 720.0, 721.0, np.nan])
    got = jf.within_factor(sim, 360.0, 2.0)
    assert got.tolist() == [False, False, True, True, True, True, False, False]
    # the rev-0 rule |rel| <= 1 wrongly admitted 0 and 100 (below observed/2)
    rev0 = np.abs(sim / 360.0 - 1) <= 1.0
    assert rev0[0] and rev0[1] and not got[0] and not got[1]
    with pytest.raises(jf.DataContractError):
        jf.within_factor(sim, 0.0, 2.0)
    with pytest.raises(jf.DataContractError):
        jf.within_factor(sim, 360.0, 1.0)
