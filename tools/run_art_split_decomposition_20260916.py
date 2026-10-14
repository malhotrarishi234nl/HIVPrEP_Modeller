#!/usr/bin/env python3
"""ART-split decomposition of diagnosed-stage AIDS progression (to-do item 3, 2026-09-16).

Not a history-matching wave. Re-simulates a small, pre-declared set of parameter vectors
from the verified diagnostic run, with the SAME CRN seeds (92000-92009), and exports the
new passive counters: diagnosed Chronic->AIDS progression off/on ART and the matching
diagnosed Chronic person-years. Outputs implied annual progression hazards per
person-year, next to each vector's parameter values (converted to hazards).

Selection rule (declared before running, deterministic):
  * the 5 lowest-I_max vectors in the diagnostic run (primary scoring), and
  * the vectors nearest the 5%, 25%, 50%, 75%, 95% quantiles of chronic_to_aids_on_art
    across all 400 design points (ties/duplicates skipped to the next nearest).

Regression check: because the new counters consume no random numbers, legacy outputs
(new_diagnoses_msm, aids_first_diagnosis_proxy_msm, new_aids_cases) must reproduce the
diagnostic run's 10-replicate means exactly. Any mismatch is reported and flagged.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

RUN = REPO / "outputs/calibration/hm_msm_aids_diagnostic_20260916"
ANALYSIS = REPO / "outputs/calibration/hm_msm_aids_diagnostic_20260916_analysis"
SEEDS = list(range(92000, 92010))
QUANTILES = (0.05, 0.25, 0.50, 0.75, 0.95)
REGRESSION_OUTPUTS = ("new_diagnoses_msm", "aids_first_diagnosis_proxy_msm", "new_aids_cases")
SPLIT_FIELDS = (
    "aids_progression_while_diagnosed_off_art", "aids_progression_while_diagnosed_on_art",
    "chronic_diagnosed_off_art_person_years", "chronic_diagnosed_on_art_person_years",
    "aids_progression_while_undiagnosed", "hiv_diagnoses_in_aids_stage",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified_inputs() -> tuple[dict, pd.DataFrame, dict, dict, list[str]]:
    manifest = json.loads((RUN / "manifest.json").read_text())
    for entry in manifest["artefacts"]:
        if entry["path"] == "wave_01/evaluated.csv" and sha(RUN / entry["path"]) != entry["sha256"]:
            raise SystemExit("REFUSING: evaluated.csv failed manifest verification")
    ip = manifest["input_provenance"]
    for name in ("base_config", "calib_space", "wave_plan"):
        local = REPO / "configs" / Path(ip[name]["path"]).name
        if sha(local) != ip[name]["sha256"]:
            raise SystemExit(f"REFUSING: {name} changed since the diagnostic run")
    base = json.loads((REPO / "configs" / Path(ip["base_config"]["path"]).name).read_text())
    space = json.loads((REPO / "configs" / Path(ip["calib_space"]["path"]).name).read_text())
    plan = json.loads((REPO / "configs" / Path(ip["wave_plan"]["path"]).name).read_text())
    if [int(s) for s in SEEDS] != [plan["run_settings"]["simulation_seed"] + i for i in range(10)]:
        raise SystemExit("REFUSING: seed list does not match the diagnostic run")
    evaluated = pd.read_csv(RUN / "wave_01/evaluated.csv")
    names = [p["name"] for p in space["parameters"]]
    return base, evaluated, space, plan, names


def select_points(evaluated: pd.DataFrame) -> pd.DataFrame:
    blocks = pd.read_csv(ANALYSIS / "block_implausibility.csv", index_col="design_row")
    chosen: list[tuple[int, str]] = [(int(i), "lowest_i_max") for i in blocks["i_max"].nsmallest(5).index]
    values = evaluated["chronic_to_aids_on_art"]
    for q in QUANTILES:
        target = values.quantile(q)
        for row in (values - target).abs().sort_values().index:
            if int(row) not in {c for c, _ in chosen}:
                chosen.append((int(row), f"on_art_quantile_{q:.2f}"))
                break
    frame = evaluated.loc[[c for c, _ in chosen]].copy()
    frame.insert(0, "selection_reason", [r for _, r in chosen])
    frame.insert(0, "design_row", [c for c, _ in chosen])
    frame["i_max_diagnostic_run"] = blocks.loc[frame["design_row"], "i_max"].to_numpy()
    return frame


def worker(payload: dict) -> list[dict]:
    from calib_extractor import extract_annual_outputs  # noqa: PLC0415
    from run_lhs_calibration import CALIB_END_YEAR, _run_simulation_quietly, inject_params  # noqa: PLC0415

    config = inject_params(payload["base"], payload["params"], payload["space"],
                           deterministic_mode=False, simulation_start_year=payload["start_year"])
    result = _run_simulation_quietly(copy.deepcopy(config), seed=payload["seed"])
    outputs = extract_annual_outputs(result, payload["scoring_start_year"], CALIB_END_YEAR)
    rows = []
    for field, series in outputs.items():
        for year, value in series.items():
            rows.append({"design_row": payload["design_row"], "seed": payload["seed"],
                         "output": field, "year": int(year), "value": float(value)})
    return rows


def hazard(p: float) -> float:
    return float(-np.log1p(-p))


def summarise(raw: pd.DataFrame, selected: pd.DataFrame, evaluated: pd.DataFrame, out: Path) -> dict:
    wide = raw.pivot_table(index=["design_row", "seed", "year"], columns="output", values="value").reset_index()
    rows = []
    for suffix, group in (("_msm", "msm"), ("_non_msm", "non_msm"), ("", "total")):
        for design_row, block in wide.groupby("design_row"):
            params = selected.set_index("design_row").loc[design_row]
            for year, yb in block.groupby("year"):
                ev_off = yb[f"aids_progression_while_diagnosed_off_art{suffix}"].sum()
                ev_on = yb[f"aids_progression_while_diagnosed_on_art{suffix}"].sum()
                py_off = yb[f"chronic_diagnosed_off_art_person_years{suffix}"].sum()
                py_on = yb[f"chronic_diagnosed_on_art_person_years{suffix}"].sum()
                rows.append({
                    "design_row": design_row, "selection_reason": params["selection_reason"],
                    "group": group, "year": int(year), "n_seeds": len(yb),
                    "events_off_art_per_seed": ev_off / len(yb), "py_off_art_per_seed": py_off / len(yb),
                    "events_on_art_per_seed": ev_on / len(yb), "py_on_art_per_seed": py_on / len(yb),
                    "implied_hazard_off_art": ev_off / py_off if py_off > 0 else np.nan,
                    "implied_hazard_on_art": ev_on / py_on if py_on > 0 else np.nan,
                    "param_hazard_off_art": hazard(params["chronic_to_aids"]),
                    "param_hazard_on_art": hazard(params["chronic_to_aids_on_art"]),
                    "on_art_share_of_diagnosed_chronic_py": py_on / (py_on + py_off) if (py_on + py_off) > 0 else np.nan,
                    "on_art_share_of_post_dx_events": ev_on / (ev_on + ev_off) if (ev_on + ev_off) > 0 else np.nan,
                })
    rates = pd.DataFrame(rows)
    rates.to_csv(out / "implied_progression_hazards.csv", index=False)

    checks = []
    for output in REGRESSION_OUTPUTS:
        for design_row, block in wide.groupby("design_row"):
            for year, yb in block.groupby("year"):
                column = f"{output}_{year}"
                if column not in evaluated.columns:
                    continue
                new = float(yb[output].mean())
                old = float(evaluated.loc[design_row, column])
                checks.append({"design_row": design_row, "output": output, "year": int(year),
                               "diagnostic_run_mean": old, "rerun_mean": new, "abs_diff": abs(new - old)})
    check = pd.DataFrame(checks)
    check.to_csv(out / "regression_check_against_diagnostic_run.csv", index=False)
    return {"regression_max_abs_diff": float(check["abs_diff"].max()),
            "regression_exact": bool((check["abs_diff"] <= 1e-9).all()),
            "n_rate_rows": int(len(rates))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="outputs/calibration/art_split_decomposition_20260916")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    base, evaluated, space, plan, names = verified_inputs()
    selected = select_points(evaluated)
    out = REPO / args.output_dir
    preview = {"n_points": len(selected), "n_simulations": len(selected) * len(SEEDS), "seeds": SEEDS,
               "selected": selected[["design_row", "selection_reason", "chronic_to_aids", "chronic_to_aids_on_art",
                                     "i_max_diagnostic_run"]].round(5).to_dict("records")}
    if args.validate_only:
        print(json.dumps({"status": "VALID -- no simulations run", **preview}, indent=2, default=float))
        return 0
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"Refusing to write into non-empty {out}")
    out.mkdir(parents=True, exist_ok=True)
    selected.to_csv(out / "selected_points.csv", index=False)
    settings = plan["run_settings"]
    payloads = [{"design_row": int(r.design_row), "seed": s, "base": base, "space": space,
                 "params": {n: float(getattr(r, n)) for n in names},
                 "start_year": settings["simulation_start_year"], "scoring_start_year": settings["scoring_start_year"]}
                for r in selected.itertuples() for s in SEEDS]
    rows, done = [], 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(worker, p) for p in payloads]):
            rows.extend(future.result())
            done += 1
            print(f"decomposition: {done}/{len(payloads)} simulations", flush=True)
    raw = pd.DataFrame(rows)
    raw = raw[raw["output"].isin(set(REGRESSION_OUTPUTS) | {f + s for f in SPLIT_FIELDS for s in ("", "_msm", "_non_msm")}
                                 | {"on_art_stock", "new_diagnoses"})]
    raw.to_csv(out / "raw_replicates_long.csv", index=False)
    result = summarise(raw, selected, evaluated, out)
    modules = ["run_v3.py", "aids_observation.py", "calib_extractor.py", "run_lhs_calibration.py", "counter_based_rng.py"]
    manifest = {"generated_utc": datetime.now(timezone.utc).isoformat(), "argv": sys.argv, **preview, **result,
                "source_run_manifest_sha256": sha(RUN / "manifest.json"),
                "module_sha256": {m: sha(REPO / m) for m in modules},
                "artefacts": {p.name: sha(p) for p in sorted(out.glob("*.csv"))}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=float))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
