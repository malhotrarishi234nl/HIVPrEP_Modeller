#!/usr/bin/env python3
"""Post-hoc analysis of the 2026-09-16 MSM-AIDS diagnostic wave.

Read-only on the run. Every headline number in DIAGNOSTIC_FINDINGS.md is computed
here and written to ``summary.json`` / CSVs in the analysis directory.

Provenance guard: the scoring inputs (wave plan, calibration space, target register)
and the scoring modules must hash-match the run manifest, and the manifest's own
artefact hashes for evaluated.csv are verified. Simulation modules (run_v3.py,
aids_observation.py, ...) are NOT required to match because nothing is re-simulated;
their current-vs-run differences are recorded, not hidden.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import hm_target_register as tr  # noqa: E402
import run_history_matching as rhm  # noqa: E402

SCORING_INPUTS = ("wave_plan", "calib_space", "target_register")
SCORING_MODULES = ("run_history_matching.py", "hm_target_register.py", "hm_variance_budget.py",
                   "hm_implausibility.py", "hm_diagnostics.py")
RATIO_YEARS = (2000, 2005, 2010, 2015, 2019, 2022)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(run: Path) -> dict:
    manifest = json.loads((run / "manifest.json").read_text())
    inputs = manifest["input_provenance"]
    checked = {}
    for name in SCORING_INPUTS:
        path = Path(inputs[name]["path"])
        local = REPO / "configs" / path.name
        if sha(local) != inputs[name]["sha256"]:
            raise SystemExit(f"REFUSING: {name} {local} differs from the run manifest")
        checked[name] = inputs[name]["sha256"]
    run_modules = inputs["runtime"]["modules"]
    for module in SCORING_MODULES:
        if sha(REPO / module) != run_modules[module]:
            raise SystemExit(f"REFUSING: scoring module {module} changed since the run")
    for entry in manifest["artefacts"]:
        if entry["path"] == "wave_01/evaluated.csv" and sha(run / entry["path"]) != entry["sha256"]:
            raise SystemExit("REFUSING: evaluated.csv does not match its manifest hash")
    changed = sorted(m for m, h in run_modules.items() if (REPO / m).exists() and sha(REPO / m) != h)
    return {"verified_inputs": checked, "verified_scoring_modules": list(SCORING_MODULES),
            "simulation_modules_changed_since_run_not_used": changed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="outputs/calibration/hm_msm_aids_diagnostic_20260916")
    parser.add_argument("--out", default="outputs/calibration/hm_msm_aids_diagnostic_20260916_analysis")
    args = parser.parse_args(argv)
    run, out = REPO / args.run, REPO / args.out
    out.mkdir(parents=True, exist_ok=True)
    summary: dict = {"provenance": verify(run)}

    plan = json.loads((REPO / "configs/hm_wave_plan_msm_aids_diagnostic_20260916.json").read_text())
    register = tr.load_register(REPO / "configs/hm_target_register_rivm_msm_aids_20260916.json")
    z = {t.key: t.z for t in register.targets}
    ev = pd.read_csv(run / "wave_01/evaluated.csv")
    k = int(plan["run_settings"]["replicates_per_design_point"])
    targets = register.restrict_years(
        [t for t in register.calibration_targets() if t.series_name in plan["waves"][0]["target_series"]],
        plan["scored_years"],
    )
    core = rhm._score_matrix(ev, targets, register, disc_multiplier=plan["discrepancy"]["multiplier"],
                             disc_method=plan["discrepancy"]["method"], v_ensemble=None,
                             ensemble_mode=rhm.PREDICTIVE_LOCAL, replicates=k)
    M = pd.DataFrame(core["matrix"], columns=core["names"])
    imax = M.max(axis=1)
    blocks = {"ecdc_hiv": "ecdc_hiv_confirmed_cases", "shm_msm": "shm_new_diagnoses_msm", "rivm_aids_msm": "rivm_aids_msm"}
    B = pd.DataFrame({b: M[[c for c in M if c.startswith(p + "@")]].max(axis=1) for b, p in blocks.items()})
    B["i_max"] = imax
    B.to_csv(out / "block_implausibility.csv", index_label="design_row")

    summary["i_max_quantiles"] = imax.quantile([0, .01, .05, .5]).round(3).to_dict()
    summary["block_min_i_max"] = B.drop(columns="i_max").min().round(3).to_dict()
    summary["block_n_le_3"] = (B.drop(columns="i_max") <= 3).sum().astype(int).to_dict()
    summary["pairwise_min_i_max"] = {
        f"{a}+{b}": round(float(B[[a, b]].max(axis=1).min()), 3)
        for a, b in (("shm_msm", "rivm_aids_msm"), ("shm_msm", "ecdc_hiv"), ("ecdc_hiv", "rivm_aids_msm"))
    }
    best = imax.nsmallest(8).index
    M.loc[best].T.to_csv(out / "best8_target_implausibility.csv")

    # Per-target simulated means at the best points and dispersion
    rows = []
    for t in core["resolved"]:
        mean = core["simulated"][t.key]
        s = core["per_run"][t.key]
        vm = s / np.maximum(mean, 1.0)
        rows.append({"target": t.key, "z": t.z, "best8_mean_min": float(mean[best].min()),
                     "best8_mean_max": float(mean[best].max()),
                     "design_median_var_over_mean": float(np.nanmedian(vm)),
                     "best8_max_var_over_mean": float(np.nanmax(vm[best]))})
    pd.DataFrame(rows).to_csv(out / "target_means_and_dispersion.csv", index=False)

    nonaids = B[["shm_msm", "ecdc_hiv"]].max(axis=1)
    top40 = nonaids.nsmallest(40).index
    summary["top40_by_non_aids_fit_i_max_range"] = [float(nonaids[top40].min()), float(nonaids[top40].max())]
    summary["top40_shm_msm_1996_median"] = float(ev.loc[top40, "new_diagnoses_msm_1996"].median())

    # AIDS proxy / same-year new diagnoses. CAVEAT: numerator draws on the diagnosed pool
    # accumulated over earlier years; this ratio does NOT remove epidemic history.
    ratio_rows = []
    params = ["chronic_to_aids", "chronic_to_aids_on_art", "art_init_2015", "art_dropout_rate",
              "diagnosis_2019", "msm_diag_mult_2019"]
    for y in RATIO_YEARS:
        data_ratio = z[f"rivm_aids_msm@{y}"] / z[f"shm_new_diagnoses_msm@{y}"]
        r = ev[f"aids_first_diagnosis_proxy_msm_{y}"] / ev[f"new_diagnoses_msm_{y}"]
        post_dx = ev[f"aids_progression_while_diagnosed_msm_{y}"] / ev[f"aids_first_diagnosis_proxy_msm_{y}"]
        rho = ev[params].corrwith(np.log(r), method="spearman")
        ratio_rows.append({"year": y, "data_ratio": data_ratio, "model_median": float(r.median()),
                           "model_p10": float(r.quantile(.1)), "model_p90": float(r.quantile(.9)),
                           "n_points_le_data": int((r <= data_ratio).sum()),
                           "median_share_post_diagnosis_progression": float(post_dx.median()),
                           **{f"spearman_{p}": float(rho[p]) for p in params}})
    pd.DataFrame(ratio_rows).to_csv(out / "aids_to_diagnosis_ratio.csv", index=False)

    cv = []
    for y in (1995, 2000, 2005, 2010, 2015, 2019, 2022):
        cv.append({"year": y, "z_non_msm": z[f"rivm_aids_non_msm@{y}"],
                   "top40_model_median": float(ev.loc[top40, f"aids_first_diagnosis_proxy_non_msm_{y}"].median())})
    pd.DataFrame(cv).to_csv(out / "non_msm_aids_cross_validation.csv", index=False)

    sens = pd.read_csv(run / "wave_01/sensitivity_output_variance_10pct/variance_budget.csv")
    sens = sens[["target_key", "v_disc_sigma", "v_ensemble_sigma", "total_sigma"]].assign(
        z=lambda d: d["target_key"].map(z), disc_sigma_over_z=lambda d: d["v_disc_sigma"] / d["z"])
    sens.to_csv(out / "output_variance_sensitivity_sigmas.csv", index=False)
    summary["run_nroy"] = json.loads((run / "wave_01/wave_summary.json").read_text())["n_nroy"]
    summary["variance_contract_stability"] = json.loads((run / "wave_01/variance_contract_stability.json").read_text())
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print(json.dumps(summary, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
