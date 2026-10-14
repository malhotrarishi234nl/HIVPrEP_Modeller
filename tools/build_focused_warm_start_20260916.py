#!/usr/bin/env python3
"""Declared warm-start proposals for the focused diagnoses-first sequence (2026-09-16).

Selection (fixed before any focused run):
  * 20 lowest SHM-MSM-diagnosis-block I_max vectors from the 2026-09-16 diagnostic run
    (diagnoses only; AIDS fit is deliberately NOT used to select), and
  * Wave-2 designs 25 and 342 of hm_focused_continuation_20260915 (the two vectors retained
    under both target-relative sensitivities there).
Mapping to the 33-parameter space: chronic_to_aids_on_art x ->
  early = clip(x, 0.002, 0.030), recent = clip(x, 0.002, 0.008). Clipping is recorded per row.
These rows PROPOSE only; the focused wave's LHS still spans the full prior box.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "configs/hm_focused_warm_start_20260916.csv"
DIAG = REPO / "outputs/calibration/hm_msm_aids_diagnostic_20260916"
BLOCKS = REPO / "outputs/calibration/hm_msm_aids_diagnostic_20260916_analysis/block_implausibility.csv"
WAVE2 = REPO / "outputs/calibration/hm_focused_continuation_20260915/wave_02/evaluated.csv"
SPACE = REPO / "configs/calib_space_v13_15_1975_orientation_seed_abc_onart_two_era_20260916.json"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    space = json.loads(SPACE.read_text())
    names = [p["name"] for p in space["parameters"]]
    blocks = pd.read_csv(BLOCKS, index_col="design_row")
    diag = pd.read_csv(DIAG / "wave_01/evaluated.csv")
    top = blocks["shm_msm"].nsmallest(20).index
    a = diag.loc[top].copy()
    a["warm_start_source"] = [f"diagnostic_20260916_row{i}_shm_block_imax{blocks.loc[i, 'shm_msm']:.2f}" for i in top]
    w2 = pd.read_csv(WAVE2)
    b = w2[w2["design_id"].isin([25, 342])].copy()
    b["warm_start_source"] = [f"focused_continuation_20260915_wave2_design{int(d)}" for d in b["design_id"]]
    rows = pd.concat([a, b], ignore_index=True)
    x = rows["chronic_to_aids_on_art"].astype(float)
    rows["chronic_to_aids_on_art_early"] = x.clip(0.002, 0.030)
    rows["chronic_to_aids_on_art_recent"] = x.clip(0.002, 0.008)
    rows["on_art_clipped"] = (rows["chronic_to_aids_on_art_early"] != x) | (rows["chronic_to_aids_on_art_recent"] != x)
    for p in space["parameters"]:
        outside = ~rows[p["name"]].between(p["min"], p["max"])
        if outside.any():
            raise SystemExit(f"{p['name']} outside prior box in rows {list(rows.index[outside])}")
    rows[names + ["warm_start_source", "chronic_to_aids_on_art", "on_art_clipped"]].to_csv(OUT, index=False)
    print(json.dumps({"path": str(OUT.relative_to(REPO)), "sha256": sha(OUT), "n_rows": len(rows),
                      "n_on_art_clipped": int(rows["on_art_clipped"].sum()),
                      "inputs": {"diagnostic_evaluated": sha(DIAG / "wave_01/evaluated.csv"),
                                 "block_implausibility": sha(BLOCKS), "wave2_evaluated": sha(WAVE2),
                                 "calib_space": sha(SPACE)}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
