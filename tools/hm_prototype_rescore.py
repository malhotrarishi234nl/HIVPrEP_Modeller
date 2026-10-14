#!/usr/bin/env python3
"""
HM-3 / task B-3 -- prototype the implausibility measure on existing sweeps.

Same simulator evaluations, new acceptance rule, no new compute.

SCOPE LIMIT -- READ THIS BEFORE USING ANY NUMBER THIS SCRIPT PRODUCES
---------------------------------------------------------------------
Every particle CSV in this repository was generated with the PRE-FIX
``demography.py`` (cellwise rounding, corrected by ENG-01 and shown by A-11 to be
materially defective).  The simulated outputs in those files come from a model
that no longer exists.  This script therefore tests whether the implausibility
machinery behaves sensibly.  It does NOT produce an NROY set.  No NROY set
derived from these particles may be carried into HM-5, HM-6, B-6, B-7 or any
result (build spec section 9; handoff B-3 scope limit of 2026-09-10).

Usage
-----
    python3 tools/hm_prototype_rescore.py --output-dir outputs/calibration/hm_prototype_<date>

"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import hm_diagnostics as hd            # noqa: E402
import hm_implausibility as hm         # noqa: E402
import hm_target_register as tr        # noqa: E402
import hm_variance_budget as vb        # noqa: E402


#: Particle sweeps named by the build spec section 9.  ``expected_rows`` is what the
#: spec says each holds; a mismatch is reported rather than assumed away.
PARTICLE_SOURCES = [
    {
        "label": "abc_smc_v13_23_snpe_bootstrap",
        "path": "outputs/calibration/abc_smc_v13_23_snpe_bootstrap/abc_smc_particles_20260822_205519.csv",
        "expected_rows": 4000,
    },
    {
        "label": "abc_smc_v13_22_linear_2019_recent_diag_solid",
        # The spec records this path relative to the project root, not the model dir.
        "path": "../data/model-outputs/v11_outputs/calibration/abc_smc_v13_22_linear_2019_recent_diag_solid_20260604/abc_smc_particles_20260604_065622.csv",
        "expected_rows": 20000,
    },
]

DISC_MULTIPLIERS = [0.01, 0.0316, 0.10, 0.316, 1.0, 3.16, 10.0]
DELAY_FRACTIONS = [0.0, 0.25, 0.5, 1.0, 2.0]


def git_state() -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
            ).stdout.strip()
        except Exception:  # pragma: no cover - provenance is best-effort
            return "unavailable"

    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def load_particles(source: dict) -> tuple[pd.DataFrame | None, dict]:
    path = (REPO_ROOT / source["path"]).resolve()
    record = {"label": source["label"], "path": str(path), "expected_rows": source["expected_rows"]}
    if not path.exists():
        record.update({"usable": False, "reason": "file not found"})
        return None, record
    frame = pd.read_csv(path, low_memory=False)
    series_columns = [c for c in frame.columns if any(c.startswith(f"{o}_") for o in
                      ("PLHIV", "new_infections", "new_diagnoses", "aids_deaths", "on_art_stock",
                       "new_aids_cases", "plhiv_female_15plus", "plhiv_male_15plus"))]
    record.update({"n_rows": int(len(frame)), "n_columns": int(frame.shape[1]),
                   "n_output_series_columns": len(series_columns)})
    if not series_columns:
        record.update({"usable": False,
                       "reason": "carries no per-year simulator output columns -- only parameters, "
                                 "gate metrics and distances, so there is nothing to rescore"})
        return None, record
    record["usable"] = True
    return frame, record


def score(
    frame: pd.DataFrame,
    targets: list[tr.ScalarTarget],
    defaults: dict,
    *,
    disc_multiplier: float | None = None,
) -> tuple[hm.CombinedImplausibility, np.ndarray, list[tr.ScalarTarget], dict, list[dict]]:
    simulated, resolved, unresolved = tr.resolve_simulated(targets, frame)
    if not resolved:
        raise SystemExit("No targets could be resolved against this particle table.")
    budgets = tr.build_budgets(resolved, simulated, defaults=defaults, disc_multiplier=disc_multiplier)
    matrix, names = hm.implausibility_matrix(
        {t.key: simulated[t.key] for t in resolved},
        {t.key: t.z for t in resolved},
        {t.key: budgets[t.key].total_variance for t in resolved},
        target_order=[t.key for t in resolved],
    )
    return hm.combine(matrix, names), matrix, resolved, budgets, unresolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--register", default=None)
    parser.add_argument("--cutoff", type=float, default=hm.IMPLAUSIBILITY_CUTOFF)
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SystemExit(f"Refusing to write into a non-empty directory: {out_dir}")
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    register = tr.load_register(args.register)
    defaults = register.meta["defaults_effective"]

    provenance = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git": git_state(),
        "register": str(Path(args.register) if args.register else tr.DEFAULT_REGISTER_PATH),
        "cutoff": args.cutoff,
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scope_limit": (
            "PRE-FIX PARTICLES. Every particle CSV here was generated with the defective "
            "pre-2026-09-06 demography.py. This is a methods dry-run of the implausibility "
            "machinery only. No NROY set derived from it may enter HM-5, HM-6, B-6, B-7 or any result."
        ),
    }

    register.as_table().to_csv(raw_dir / "target_register_scalar.csv", index=False)
    register.series_summary().to_csv(raw_dir / "target_register_series.csv", index=False)
    (raw_dir / "escalations.txt").write_text("\n".join(register.escalations()) or "(none)", encoding="utf-8")

    # ------------------------------------------------------------------
    # Which sweeps can actually be rescored
    # ------------------------------------------------------------------
    sources: list[dict] = []
    frames: dict[str, pd.DataFrame] = {}
    for source in PARTICLE_SOURCES:
        frame, record = load_particles(source)
        sources.append(record)
        if frame is not None:
            frames[record["label"]] = frame
    pd.DataFrame(sources).to_csv(raw_dir / "particle_sources.csv", index=False)
    if not frames:
        raise SystemExit("No particle sweep carries per-year simulator outputs; nothing to rescore.")

    label, frame = next(iter(frames.items()))

    calibration = register.calibration_targets()
    excluded = register.by_role(["excluded_pending_c1"])
    cross_validation = register.cross_validation_targets()

    results: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Two declared target sets: the pre-C-1 set and the post-C-1 set
    # ------------------------------------------------------------------
    target_sets = {
        "pre_c1_with_non_msm": calibration + excluded,
        "post_c1_without_non_msm": calibration,
    }

    for set_name, targets in target_sets.items():
        combined, matrix, resolved, budgets, unresolved = score(frame, targets, defaults)
        prefix = f"{set_name}"
        hd.nroy_summary(combined, cutoff=args.cutoff).to_csv(raw_dir / f"{prefix}__nroy_summary.csv", index=False)
        per_target = hd.per_target_summary(matrix, resolved, budgets, cutoff=args.cutoff)
        per_target.to_csv(raw_dir / f"{prefix}__per_target_implausibility.csv", index=False)
        hd.binding_target_counts(combined).to_csv(raw_dir / f"{prefix}__binding_targets.csv", index=False)
        pd.DataFrame(vb.budget_table(budgets.values())).to_csv(raw_dir / f"{prefix}__variance_budget.csv", index=False)

        # --- REQUIRED: ensemble size vs V_disc -------------------------
        curve = hd.discrepancy_sensitivity_curve(
            DISC_MULTIPLIERS,
            lambda m, t=targets: score(frame, t, defaults, disc_multiplier=m)[0],
            cutoff=args.cutoff,
        )
        curve.to_csv(raw_dir / f"{prefix}__vdisc_curve.csv", index=False)

        # --- sensitivity to the one declared assumption in V_obs -------
        def delay_score(fraction: float, t=targets) -> hm.CombinedImplausibility:
            local = tr.load_register(args.register, overrides={"shm_delay_uncertainty_fraction": fraction})
            local_targets = [x for x in local.targets if x.key in {y.key for y in t}]
            return score(frame, local_targets, local.meta["defaults_effective"])[0]

        delay_curve = hd.generic_sensitivity_curve("shm_delay_uncertainty_fraction", DELAY_FRACTIONS, delay_score, cutoff=args.cutoff)
        delay_curve.to_csv(raw_dir / f"{prefix}__delay_fraction_curve.csv", index=False)

        # --- granularity ----------------------------------------------
        years_by_series: dict[int, None] = {}
        for target in resolved:
            years_by_series.setdefault(target.year, None)
        all_years = sorted(years_by_series)
        year_sets = {
            "all_scored_years": None,
            "recent_5_years": all_years[-5:],
            "latest_year_only": all_years[-1:],
        }
        granularity = hd.granularity_comparison(
            lambda years, t=targets: score(frame, register.restrict_years(t, years), defaults)[0],
            year_sets,
            cutoff=args.cutoff,
        )
        granularity.to_csv(raw_dir / f"{prefix}__granularity.csv", index=False)

        parameter_columns = [c for c in frame.columns if c in _prior_bounds(register)]
        volume = hd.nroy_volume(frame, combined.nroy_mask(cutoff=args.cutoff), _prior_bounds(register))
        hd.per_parameter_range_table(volume).to_csv(raw_dir / f"{prefix}__parameter_ranges.csv", index=False)

        results[set_name] = {
            "n_scalar_targets_requested": len(targets),
            "n_scalar_targets_resolved": len(resolved),
            "unresolved": unresolved,
            "nroy": hd.nroy_summary(combined, cutoff=args.cutoff).to_dict("records"),
            "top_binding": hd.binding_target_counts(combined).head(10).to_dict("records"),
            "worst_targets": per_target.head(10).to_dict("records"),
            "vdisc_curve": curve.to_dict("records"),
            "delay_curve": delay_curve.to_dict("records"),
            "granularity": granularity.to_dict("records"),
            "volume": {k: v for k, v in volume.items() if k != "per_parameter_range_fraction"},
            "n_parameter_columns_matched": len(parameter_columns),
        }

    # ------------------------------------------------------------------
    # Cross-validation comparators, reported separately -- never in I_max
    # ------------------------------------------------------------------
    cv_combined, cv_matrix, cv_resolved, cv_budgets, cv_unresolved = score(frame, cross_validation, defaults)
    hd.per_target_summary(cv_matrix, cv_resolved, cv_budgets, cutoff=args.cutoff).to_csv(
        raw_dir / "cross_validation__per_target_implausibility.csv", index=False
    )
    pd.DataFrame(vb.budget_table(cv_budgets.values())).to_csv(raw_dir / "cross_validation__variance_budget.csv", index=False)
    results["cross_validation"] = {
        "n_scalar_targets_resolved": len(cv_resolved),
        "unresolved": cv_unresolved,
        "note": "Tier-2 comparators. Reported, never combined into I_max (register role = cross_validation).",
        "nroy_if_it_had_been_a_target": hd.nroy_summary(cv_combined, cutoff=args.cutoff).to_dict("records"),
    }

    payload = {"provenance": provenance, "particle_sources": sources, "scored_sweep": label, "results": results}
    (out_dir / "prototype_results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output_dir": str(out_dir), "scored_sweep": label,
                      "sets": {k: v.get("nroy") for k, v in results.items() if "nroy" in v}}, indent=2, default=str))
    return 0


def _prior_bounds(register: tr.TargetRegister) -> dict[str, tuple[float, float]]:
    """Prior box from configs/calib_space.json.

    NOTE: which space is canonical is UNRESOLVED (task B-1: calib_space.json
    declares 27 parameters, calib_space_v13_15_1975_orientation_seed_abc.json
    declares 32, and at least one bound differs).  The volume figures this
    produces are therefore provisional and are labelled as such in the report.
    """
    path = REPO_ROOT / "configs" / "calib_space.json"
    with path.open(encoding="utf-8") as handle:
        space = json.load(handle)
    bounds: dict[str, tuple[float, float]] = {}
    for parameter in space.get("parameters", []):
        if parameter.get("min") is None or parameter.get("max") is None:
            continue
        bounds[parameter["name"]] = (float(parameter["min"]), float(parameter["max"]))
        companion = parameter.get("derived_companion")
        if companion and companion.get("min") is not None:
            bounds[companion["name"]] = (float(companion["min"]), float(companion["max"]))
    return bounds


if __name__ == "__main__":
    raise SystemExit(main())
