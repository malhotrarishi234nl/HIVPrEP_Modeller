#!/usr/bin/env python3
"""A-18 section 5: light probes of the three proposed remedies.

Each probe answers "is this worth building?" and nothing more.  No remedy is
adopted here, no production behaviour changes, and no tolerance, threshold,
bound or parameter is altered.

  5.1 remedy A -- run calibration stochastically.  No new model runs: the
      implausibility at the one near-fit parameter point is scored twice, once
      on the deterministic trajectory and once on the stochastic mean, and the
      denominator effect is quantified in BOTH directions.

  5.2 remedy B -- carry the measured gap as a declared discrepancy term.  The
      term is derived from the measured deterministic-versus-stochastic gap,
      WRITTEN DOWN FIRST, then used once to rescore the existing 4,000
      particles.  Whatever NROY results is the answer.  The term is never
      re-derived, scaled or widened afterwards.  This probe is exempt from the
      section 3 stop rule because the gap is measured whether or not Jensen
      explains it.

  5.3 remedy C -- a second-order (Jensen) correction, as an opt-in diagnostic
      variant, production default unchanged.

SCOPE LIMIT inherited from the HM prototype: every particle in
``outputs/calibration/hm_prototype_20260910`` was generated with the pre-fix
``demography.py``.  Probe 5.2's NROY counts test the machinery, not the model.
No NROY set from them may enter HM-5, HM-6, B-6, B-7 or any result.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import platform
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import hm_diagnostics as hd  # noqa: E402
import hm_implausibility as hm  # noqa: E402
import hm_target_register as tr  # noqa: E402
import hm_variance_budget as vb  # noqa: E402
from calib_extractor import extract_annual_outputs  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402
from tools.a17_residual_placement import METRICS, compare_annual_levels  # noqa: E402
from tools.a18_jensen_mechanism import (  # noqa: E402
    CORRECTION_VARIANTS,
    HybridTransmission,
    TransmissionTrace,
    contact_rounding_variance,
    second_order_curvature,
)

START_YEAR = 1975
END_YEAR = 2025
PARTICLES = (
    "outputs/calibration/abc_smc_v13_23_snpe_bootstrap/"
    "abc_smc_particles_20260822_205519.csv"
)

#: Design assumptions of the real stochastic wave, quoted from the A-18 brief
#: section 5.1 so the compute arithmetic is reproducible and attributable.
BRIEF_DESIGN_POINTS = 4000
BRIEF_REPLICATES_PER_POINT = 15
BRIEF_CORE_HOURS = 1000
BRIEF_VCPUS = 96
#: The HM build spec's per-run figure, quoted in the V_code derivation string.
SPEC_SECONDS_PER_RUN = 60.0

#: Multiples of the DERIVED sigma scanned solely to report the magnitude that
#: would be required for a non-empty NROY.  Reporting only; never adopted.
SCALING_SCAN = (1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_text(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.rstrip()


def configured(base: dict, *, seed: int) -> dict:
    config = json.loads(json.dumps(base))
    config["run_control"]["deterministic_mode"] = {"value": True}
    config.setdefault("simulation_controls", {})["deterministic_mode"] = {"value": True}
    config["run_control"]["seed"] = {"value": int(seed)}
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    return config


def run_variant(base: dict, *, seed: int, variant: str | None, run_number: int, log,
                trace=None) -> dict:
    config = configured(base, seed=seed)
    context = (
        HybridTransmission(variant, trace=trace)
        if variant is not None
        else contextlib.nullcontext()
    )
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        with context:
            result = SimulationEngineV3(config, seed=seed).run(run_number=run_number)
    log.write(f"===== {variant or 'production_deterministic'} seed {seed} =====\n"
              f"{buffer.getvalue()}")
    return extract_annual_outputs(result, start_year=START_YEAR, end_year=END_YEAR)


def annual_frame(annual: dict) -> pd.DataFrame:
    rows = []
    for metric in METRICS:
        for year in range(START_YEAR, END_YEAR + 1):
            rows.append({"metric": metric, "year": year,
                         "value": float(annual[metric][year])})
    return pd.DataFrame(rows)


# ── probe 5.1 ─────────────────────────────────────────────────────────────

def probe_remedy_a(
    comparison: pd.DataFrame,
    prototype_targets: pd.DataFrame,
    *,
    replicates_per_point: int,
) -> tuple[pd.DataFrame, dict]:
    """Score the same implausibility twice at the one near-fit point.

    ``total_sigma`` is taken from the HM prototype's own per-target table, the
    same declared approximation B-8 used: V_disc is defined relative to a
    wave's output variance, so a faithful recomputation needs the whole wave.
    The table shows DIRECTION and rough SIZE, not a new implausibility estimate.
    """
    obs = prototype_targets[
        ["target_key", "series_name", "model_output", "year", "epistemic_tier",
         "role", "z", "total_sigma"]
    ].rename(columns={"model_output": "metric", "z": "observed_target"})
    merged = obs.merge(
        comparison[
            ["metric", "year", "deterministic", "stochastic_mean", "stochastic_sd",
             "n_replicates"]
        ],
        on=["metric", "year"], how="inner",
    )

    merged["v_total_current"] = merged["total_sigma"] ** 2
    merged["v_ensemble_single_run"] = merged["stochastic_sd"] ** 2
    merged["v_ensemble_mean_of_n"] = (
        merged["v_ensemble_single_run"] / float(replicates_per_point)
    )

    # Numerator effect: does removing the level inflation move the model toward
    # its targets?
    merged["I_deterministic"] = (
        (merged["observed_target"] - merged["deterministic"]).abs()
        / merged["total_sigma"]
    )
    merged["I_stochastic_mean_same_denominator"] = (
        (merged["observed_target"] - merged["stochastic_mean"]).abs()
        / merged["total_sigma"]
    )
    # Denominator effect, both directions.
    merged["I_stochastic_mean_with_v_ensemble_n1"] = (
        (merged["observed_target"] - merged["stochastic_mean"]).abs()
        / np.sqrt(merged["v_total_current"] + merged["v_ensemble_single_run"])
    )
    merged["I_stochastic_mean_with_v_ensemble_mean_of_n"] = (
        (merged["observed_target"] - merged["stochastic_mean"]).abs()
        / np.sqrt(merged["v_total_current"] + merged["v_ensemble_mean_of_n"])
    )
    merged["delta_I_numerator_only"] = (
        merged["I_stochastic_mean_same_denominator"] - merged["I_deterministic"]
    )
    merged["delta_I_full_switch_mean_of_n"] = (
        merged["I_stochastic_mean_with_v_ensemble_mean_of_n"]
        - merged["I_deterministic"]
    )
    merged["v_ensemble_share_of_new_total_mean_of_n"] = (
        merged["v_ensemble_mean_of_n"]
        / (merged["v_total_current"] + merged["v_ensemble_mean_of_n"])
    )

    summary = {
        "n_tier1_target_years_scored": int(len(merged)),
        "replicates_per_point_assumed": int(replicates_per_point),
        "median_I_deterministic": float(merged["I_deterministic"].median()),
        "median_I_stochastic_mean_same_denominator": float(
            merged["I_stochastic_mean_same_denominator"].median()
        ),
        "median_I_stochastic_mean_full_switch": float(
            merged["I_stochastic_mean_with_v_ensemble_mean_of_n"].median()
        ),
        "n_targets_numerator_improves": int((merged["delta_I_numerator_only"] < 0).sum()),
        "n_targets_numerator_worsens": int((merged["delta_I_numerator_only"] > 0).sum()),
        "n_targets_full_switch_improves": int(
            (merged["delta_I_full_switch_mean_of_n"] < 0).sum()
        ),
        "n_targets_full_switch_worsens": int(
            (merged["delta_I_full_switch_mean_of_n"] > 0).sum()
        ),
        "max_I_deterministic": float(merged["I_deterministic"].max()),
        "max_I_stochastic_mean_full_switch": float(
            merged["I_stochastic_mean_with_v_ensemble_mean_of_n"].max()
        ),
        "median_v_ensemble_share_of_new_total": float(
            merged["v_ensemble_share_of_new_total_mean_of_n"].median()
        ),
        "max_v_ensemble_share_of_new_total": float(
            merged["v_ensemble_share_of_new_total_mean_of_n"].max()
        ),
    }
    return merged.sort_values(["series_name", "year"]).reset_index(drop=True), summary


def compute_arithmetic(measured_seconds_per_run: float) -> dict:
    """Both the brief's stated compute figure and the one actually measured."""
    runs = BRIEF_DESIGN_POINTS * BRIEF_REPLICATES_PER_POINT
    measured_core_hours = runs * measured_seconds_per_run / 3600.0
    spec_core_hours = runs * SPEC_SECONDS_PER_RUN / 3600.0
    return {
        "design_points": BRIEF_DESIGN_POINTS,
        "replicates_per_point": BRIEF_REPLICATES_PER_POINT,
        "total_runs": runs,
        "brief_stated_core_hours": BRIEF_CORE_HOURS,
        "brief_stated_wall_clock_hours_on_%d_vcpus" % BRIEF_VCPUS:
            BRIEF_CORE_HOURS / BRIEF_VCPUS,
        "brief_implied_seconds_per_run": BRIEF_CORE_HOURS * 3600.0 / runs,
        "hm_spec_seconds_per_run": SPEC_SECONDS_PER_RUN,
        "hm_spec_core_hours": spec_core_hours,
        "measured_seconds_per_run_this_session": measured_seconds_per_run,
        "measured_core_hours": measured_core_hours,
        "measured_wall_clock_hours_on_%d_vcpus" % BRIEF_VCPUS:
            measured_core_hours / BRIEF_VCPUS,
    }


# ── probe 5.2 ─────────────────────────────────────────────────────────────

def derive_discrepancy_terms(
    targets, comparison: pd.DataFrame
) -> tuple[dict[str, vb.BudgetTerm], pd.DataFrame, list[dict]]:
    """V_disc increment = (measured deterministic-minus-stochastic gap)^2.

    Derived once, from a measurement that already exists, BEFORE any rescoring.
    Never adjusted afterwards -- see the A-18 brief's section 5.2 boundary note
    and handoff section 1A.4.
    """
    gaps = comparison.set_index(["metric", "year"])[
        "difference"
    ].to_dict()
    terms: dict[str, vb.BudgetTerm] = {}
    rows: list[dict] = []
    unmeasured: list[dict] = []
    for target in targets:
        key = (target.model_output, int(target.year))
        gap = gaps.get(key)
        if gap is None:
            unmeasured.append(
                {
                    "target_key": target.key,
                    "model_output": target.model_output,
                    "year": int(target.year),
                    "reason": (
                        "no measured deterministic-versus-stochastic gap exists "
                        "for this model output; contributes 0.0, which shrinks "
                        "the denominator and biases towards OVER-exclusion"
                    ),
                }
            )
            continue
        variance = float(gap) ** 2
        terms[target.key] = vb.BudgetTerm(
            name="v_disc",
            variance=variance,
            status="derived",
            derivation=(
                f"A-18 remedy B: squared MEASURED deterministic-minus-stochastic-mean "
                f"gap at the near-fit parameter point, "
                f"{target.model_output}@{int(target.year)} = {float(gap):.6g} "
                f"(n=50 stochastic replicates). Derived before rescoring and not "
                f"adjusted afterwards."
            ),
            direction="none",
        )
        rows.append(
            {
                "target_key": target.key,
                "model_output": target.model_output,
                "year": int(target.year),
                "measured_gap_det_minus_stoch_mean": float(gap),
                "v_disc_increment_variance": variance,
                "v_disc_increment_sigma": float(abs(gap)),
            }
        )
    return terms, pd.DataFrame(rows), unmeasured


def score_with_increment(
    frame: pd.DataFrame,
    targets,
    defaults: dict,
    increments: dict[str, vb.BudgetTerm] | None,
):
    simulated, resolved, unresolved = tr.resolve_simulated(targets, frame)
    if not resolved:
        raise SystemExit("no target resolved against the particle table")
    budgets = tr.build_budgets(resolved, simulated, defaults=defaults)
    if increments:
        rebuilt = {}
        for target in resolved:
            budget = budgets[target.key]
            extra = increments.get(target.key)
            rebuilt[target.key] = vb.assemble(
                target.key,
                v_obs=budget.v_obs,
                v_ensemble=budget.v_ensemble,
                v_disc=budget.v_disc,
                extra_terms=tuple(budget.extra_terms)
                + ((vb.BudgetTerm(
                    name="v_disc_a18_measured_gap",
                    variance=extra.variance,
                    status=extra.status,
                    derivation=extra.derivation,
                    direction=extra.direction,
                ),) if extra is not None else ()),
            )
        budgets = rebuilt
    matrix, names = hm.implausibility_matrix(
        {t.key: simulated[t.key] for t in resolved},
        {t.key: t.z for t in resolved},
        {t.key: budgets[t.key].total_variance for t in resolved},
        target_order=[t.key for t in resolved],
    )
    return hm.combine(matrix, names), matrix, resolved, budgets, unresolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--step1-dir", type=Path, required=True,
                        help="A-18 Step 1 output directory (supplies the measured gap)")
    parser.add_argument("--a5a-dir", type=Path, required=True)
    parser.add_argument("--prototype-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--register", type=Path, default=None)
    parser.add_argument("--cutoff", type=float, default=hm.IMPLAUSIBILITY_CUTOFF)
    parser.add_argument("--seed", type=int, default=7001)
    parser.add_argument("--replicates-per-point", type=int,
                        default=BRIEF_REPLICATES_PER_POINT)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    provenance_dir = output_dir / "provenance"
    raw_dir.mkdir()
    provenance_dir.mkdir()

    status = git_text("status", "--short")
    (provenance_dir / "git_status_short.txt").write_text(
        status + ("\n" if status else ""), encoding="utf-8"
    )
    (provenance_dir / "git_diff.patch").write_bytes(
        subprocess.run(["git", "diff", "--binary", "--no-ext-diff"],
                       cwd=ROOT, check=True, capture_output=True).stdout
    )

    step1_dir = args.step1_dir.resolve()
    a5a_dir = args.a5a_dir.resolve()
    prototype_dir = args.prototype_dir.resolve()
    particles_path = (ROOT / PARTICLES).resolve()
    prototype_targets_path = (
        prototype_dir / "raw" / "post_c1_without_non_msm__per_target_implausibility.csv"
    )

    command = shlex.join([sys.executable, *sys.argv])
    manifest = {
        "task": "A-18 section 5 remedy probes",
        "command": command,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "step1_dir": str(step1_dir),
        "a5a_dir": str(a5a_dir),
        "prototype_dir": str(prototype_dir),
        "particles": str(particles_path),
        "cutoff": args.cutoff,
        "replicates_per_point": args.replicates_per_point,
        "scope_limit": (
            "PRE-FIX PARTICLES. Probe 5.2's NROY counts exercise the "
            "implausibility machinery on particles generated by the defective "
            "pre-2026-09-06 demography.py. No NROY set derived from them may "
            "enter HM-5, HM-6, B-6, B-7 or any result."
        ),
        "tuning_prohibition_procedure": (
            "The remedy-B discrepancy term is derived from the measured gap, "
            "written to raw/probe_b_derived_discrepancy_terms.csv BEFORE the "
            "rescoring runs, used exactly once, and never adjusted afterwards."
        ),
        "git_commit": git_text("rev-parse", "HEAD"),
        "git_dirty": bool(status),
        "python": sys.version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "platform": platform.platform(),
        "input_sha256": {
            "model_config": sha256(args.model_config.resolve()),
            "particles": sha256(particles_path),
            "prototype_per_target_implausibility": sha256(prototype_targets_path),
            "step1_yearly_log_ratio": sha256(
                step1_dir / "raw" / "step1_yearly_log_ratio.csv"
            ),
            "a5a_replicates": sha256(a5a_dir / "raw" / "replicates.csv"),
            "tools/a18_jensen_mechanism.py": sha256(
                ROOT / "tools/a18_jensen_mechanism.py"
            ),
            "tools/run_a18_remedies.py": sha256(Path(__file__).resolve()),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    base = json.loads(args.model_config.resolve().read_text(encoding="utf-8"))

    # ── the measured gap, recomputed against A-5a's post-CRN ensemble ─────
    step1_yearly = pd.read_csv(step1_dir / "raw" / "step1_yearly_log_ratio.csv")
    deterministic = step1_yearly[step1_yearly["arm"] == "deterministic_baseline"][
        ["metric", "year", "deterministic"]
    ].rename(columns={"deterministic": "value"})

    a5a = pd.read_csv(a5a_dir / "raw" / "replicates.csv")
    a5a_required = {"seed", "metric", "year", "value"}
    if not a5a_required.issubset(a5a.columns):
        raise SystemExit(
            f"A-5a replicates.csv lacks {sorted(a5a_required - set(a5a.columns))}"
        )
    a5a = a5a[a5a["metric"].isin(METRICS)]
    # A-5a covers only the scored target-years (1990-2024); restrict the
    # deterministic trajectory to exactly that metric/year set so the
    # comparison is like for like.
    a5a_pairs = a5a[["metric", "year"]].drop_duplicates()
    deterministic = deterministic.merge(a5a_pairs, on=["metric", "year"], how="inner")
    comparison = compare_annual_levels(deterministic, a5a)
    comparison.to_csv(raw_dir / "measured_gap_vs_a5a_post_crn.csv", index=False)

    register = tr.load_register(str(args.register) if args.register else None)
    defaults = register.meta["defaults_effective"]
    calibration = register.calibration_targets()

    # ── probe 5.2: derive and WRITE THE TERM FIRST ───────────────────────
    increments, increment_table, unmeasured = derive_discrepancy_terms(
        calibration, comparison
    )
    increment_table.to_csv(
        raw_dir / "probe_b_derived_discrepancy_terms.csv", index=False
    )
    (raw_dir / "probe_b_targets_without_measured_gap.json").write_text(
        json.dumps(unmeasured, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "A-18 probe 5.2: discrepancy term derived and written for "
        f"{len(increment_table)} targets; {len(unmeasured)} have no measured gap"
    )

    frame = pd.read_csv(particles_path, low_memory=False)

    baseline_combined, _, baseline_resolved, baseline_budgets, baseline_unresolved = (
        score_with_increment(frame, calibration, defaults, None)
    )
    rescored_combined, rescored_matrix, rescored_resolved, rescored_budgets, _ = (
        score_with_increment(frame, calibration, defaults, increments)
    )

    baseline_nroy = hd.nroy_summary(baseline_combined, cutoff=args.cutoff)
    rescored_nroy = hd.nroy_summary(rescored_combined, cutoff=args.cutoff)
    baseline_nroy.insert(0, "scoring", "baseline_no_a18_term")
    rescored_nroy.insert(0, "scoring", "with_a18_measured_gap_term")
    pd.concat([baseline_nroy, rescored_nroy], ignore_index=True).to_csv(
        raw_dir / "probe_b_nroy_summary.csv", index=False
    )
    hd.per_target_summary(
        rescored_matrix, rescored_resolved, rescored_budgets, cutoff=args.cutoff
    ).to_csv(raw_dir / "probe_b_per_target_implausibility.csv", index=False)
    pd.DataFrame(vb.budget_table(rescored_budgets.values())).to_csv(
        raw_dir / "probe_b_variance_budget.csv", index=False
    )

    # ── probe 5.2, the section 7 reporting item ──────────────────────────
    # "Remedy B's derived term makes NROY non-empty only at a magnitude larger
    # than the measured gap. Report the required magnitude and stop. Do not
    # adopt it."  This scan exists ONLY to report that magnitude.  Nothing
    # below is adopted, and the derived term written above is unchanged.
    scan_rows = []
    required_factor = None
    for factor in SCALING_SCAN:
        scaled = {
            key: vb.BudgetTerm(
                name=term.name,
                variance=term.variance * factor * factor,
                status=term.status,
                derivation=(
                    f"REPORTING SCAN ONLY, NOT ADOPTED: derived term scaled by "
                    f"{factor}x in sigma. {term.derivation}"
                ),
                direction=term.direction,
            )
            for key, term in increments.items()
        }
        combined, _, _, _, _ = score_with_increment(frame, calibration, defaults, scaled)
        summary = hd.nroy_summary(combined, cutoff=args.cutoff)
        n_max = int(summary.loc[summary["rule"] == "I_max (PRIMARY)", "n_nroy"].iloc[0])
        scan_rows.append(
            {
                "sigma_scaling_factor": factor,
                "n_nroy_max": n_max,
                "frac_nroy_max": n_max / len(frame),
                "adopted": False,
            }
        )
        if n_max > 0 and required_factor is None:
            required_factor = factor
    pd.DataFrame(scan_rows).to_csv(
        raw_dir / "probe_b_required_magnitude_scan_NOT_ADOPTED.csv", index=False
    )

    probe_b = {
        "n_particles": int(len(frame)),
        "required_sigma_scaling_for_nonempty_nroy": required_factor,
        "required_magnitude_scan": scan_rows,
        "required_magnitude_scan_is_reporting_only": (
            "Scanned to satisfy the A-18 brief section 7 reporting requirement. "
            "No scaled term is adopted, and the derived term in "
            "probe_b_derived_discrepancy_terms.csv was not altered."
        ),
        "n_targets_resolved": len(rescored_resolved),
        "n_targets_with_derived_term": int(len(increment_table)),
        "n_targets_without_measured_gap": len(unmeasured),
        "unresolved_targets": baseline_unresolved,
        "median_derived_sigma": float(
            increment_table["v_disc_increment_sigma"].median()
        ) if len(increment_table) else float("nan"),
        "min_derived_sigma": float(
            increment_table["v_disc_increment_sigma"].min()
        ) if len(increment_table) else float("nan"),
        "max_derived_sigma": float(
            increment_table["v_disc_increment_sigma"].max()
        ) if len(increment_table) else float("nan"),
        "median_share_of_new_total_variance": float(
            np.median([
                rescored_budgets[key].share("v_disc_a18_measured_gap")
                for key in increments
                if key in rescored_budgets
            ])
        ) if increments else float("nan"),
        "nroy_baseline": baseline_nroy.to_dict("records"),
        "nroy_with_term": rescored_nroy.to_dict("records"),
        "term_adjusted_after_seeing_result": False,
    }

    # ── probe 5.1 ────────────────────────────────────────────────────────
    prototype_targets = pd.read_csv(prototype_targets_path)
    probe_a_table, probe_a_summary = probe_remedy_a(
        comparison, prototype_targets, replicates_per_point=args.replicates_per_point
    )
    probe_a_table.to_csv(raw_dir / "probe_a_implausibility.csv", index=False)

    # Sensitivity to decision D-11, which is OPEN.  The register default scores
    # ecdc_hiv_confirmed_cases at 1990-1995 only; whether it should be scored at
    # all is undecided (PHASE2_DEVELOPMENT_CHECKLIST decision log, D-11).  That
    # choice turns out to determine probe 5.1's verdict, so it is reported
    # rather than left implicit.  Nothing is decided here.
    d11_rows = []
    for label, subset in [
        ("register_default_all_tier1", probe_a_table),
        (
            "excluding_ecdc_hiv_confirmed_cases",
            probe_a_table[probe_a_table["series_name"] != "ecdc_hiv_confirmed_cases"],
        ),
    ]:
        d11_rows.append(
            {
                "target_set": label,
                "n_targets": int(len(subset)),
                "median_I_deterministic": float(subset["I_deterministic"].median()),
                "median_I_stochastic_mean": float(
                    subset["I_stochastic_mean_same_denominator"].median()
                ),
                "max_I_deterministic": float(subset["I_deterministic"].max()),
                "max_I_stochastic_mean": float(
                    subset["I_stochastic_mean_same_denominator"].max()
                ),
                "n_targets_I_gt_3_deterministic": int(
                    (subset["I_deterministic"] > 3).sum()
                ),
                "n_targets_I_gt_3_stochastic_mean": int(
                    (subset["I_stochastic_mean_same_denominator"] > 3).sum()
                ),
                "switching_improves_max_I": bool(
                    subset["I_stochastic_mean_same_denominator"].max()
                    < subset["I_deterministic"].max()
                ),
            }
        )
    d11 = pd.DataFrame(d11_rows)
    d11.to_csv(raw_dir / "probe_a_d11_target_set_sensitivity.csv", index=False)
    probe_a_summary["d11_target_set_sensitivity"] = d11_rows
    probe_a_summary["d11_note"] = (
        "Decision D-11 (role of ecdc_hiv_confirmed_cases) is OPEN. At this one "
        "near-fit parameter point every target with I > 3 belongs to that "
        "series, and excluding it flips whether switching to the stochastic "
        "mean improves max I. A-18 decides nothing; it reports the dependency."
    )

    timings = pd.read_csv(step1_dir / "raw" / "run_timings.csv")
    measured_seconds = float(
        timings[timings["arm"] == "stochastic_reference"]["elapsed_seconds"].median()
    )
    probe_a_summary["compute"] = compute_arithmetic(measured_seconds)
    probe_a_summary["v_ensemble_currently_in_budget"] = {
        "status": str(
            baseline_budgets[rescored_resolved[0].key].v_ensemble.status
        ),
        "variance": float(
            baseline_budgets[rescored_resolved[0].key].v_ensemble.variance
        ),
        "note": (
            "V_ensemble is presently 0.0 with status 'underivable' for every "
            "calibration target. The brief's counter-argument -- that averaging "
            "n seeds tightens the denominator by sqrt(n) -- is stated against a "
            "baseline where V_ensemble is already carried at n=1. It is not. "
            "Moving to stochastic mode would ADD a term that is currently zero, "
            "so relative to the CURRENT budget the denominator grows and the "
            "rule excludes LESS. The sqrt(n) tightening is real only relative to "
            "a single-replicate stochastic budget, which is also reported here."
        ),
    }

    # ── probe 5.3 ────────────────────────────────────────────────────────
    with (output_dir / "runner_stdout.log").open("w", encoding="utf-8") as log:
        started = time.time()
        production = run_variant(base, seed=args.seed, variant=None, run_number=1, log=log)
        corrected = {}
        correction_timings = {}
        traces = {}
        for index, variant in enumerate(CORRECTION_VARIANTS, start=2):
            trace = TransmissionTrace(trace_years=frozenset({1990.0}))
            run_started = time.time()
            corrected[variant] = run_variant(
                base, seed=args.seed, variant=variant, run_number=index, log=log,
                trace=trace,
            )
            correction_timings[variant] = time.time() - run_started
            traces[variant] = trace.frame()
            print(f"A-18 probe 5.3: ran {variant}", flush=True)
        elapsed_c = time.time() - started

    production_frame = annual_frame(production)
    probe_c_rows = []
    probe_c = {
        "sigma_squared_source": (
            "Var(n) under the model's own contact integerisation, "
            "frac(n)*(1-frac(n)). This is the variance of the ROUNDING step, "
            "not an epidemiological contact-heterogeneity variance: the model "
            "carries no dispersion parameter for contacts and "
            "_step_contacts_and_mixing produces one deterministic float per "
            "orientation/activity pair in both run modes."
        ),
        "elapsed_seconds": elapsed_c,
        "variants": {},
    }
    for variant, annual in corrected.items():
        frame_v = annual_frame(annual)
        merged = production_frame.merge(
            frame_v, on=["metric", "year"], suffixes=("_production", "_corrected")
        )
        diff = merged["value_corrected"] - merged["value_production"]
        trace_frame = traces[variant]
        curvature = np.array([
            second_order_curvature(
                row.contacts_float, row.lambda_per_contact, row.susceptible_pop,
                form=CORRECTION_VARIANTS[variant],
            )
            for row in trace_frame.itertuples()
        ])
        sigma2 = np.array([
            contact_rounding_variance(row.contacts_float)
            for row in trace_frame.itertuples()
        ])
        correction = 0.5 * curvature * sigma2
        linear = (
            trace_frame["contacts_float"] * trace_frame["lambda_per_contact"]
        ).to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            relative = np.where(linear > 0.0, correction / linear, np.nan)
        probe_c["variants"][variant] = {
            "curvature_form": CORRECTION_VARIANTS[variant],
            "n_metric_years": int(len(merged)),
            "n_metric_years_changed": int((diff != 0).sum()),
            "bit_identical_to_production_deterministic": bool((diff == 0).all()),
            "max_abs_change": float(diff.abs().max()),
            "traced_strata": int(len(trace_frame)),
            "median_abs_second_order_term": float(np.nanmedian(np.abs(correction))),
            "max_abs_second_order_term": float(np.nanmax(np.abs(correction))),
            "median_abs_relative_to_first_order": float(
                np.nanmedian(np.abs(relative))
            ),
            "max_abs_relative_to_first_order": float(np.nanmax(np.abs(relative))),
            "elapsed_seconds": correction_timings[variant],
        }
        merged["arm"] = variant
        probe_c_rows.append(merged)
    pd.concat(probe_c_rows, ignore_index=True).to_csv(
        raw_dir / "probe_c_annual_comparison.csv", index=False
    )

    findings = {
        "probe_5_1_remedy_a": probe_a_summary,
        "probe_5_2_remedy_b": probe_b,
        "probe_5_3_remedy_c": probe_c,
    }
    (raw_dir / "findings.json").write_text(
        json.dumps(findings, indent=2, default=float) + "\n", encoding="utf-8"
    )

    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["result"] = findings
    paths = sorted(
        path for path in output_dir.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.txt"}
    )
    manifest["output_sha256"] = {
        str(p.relative_to(output_dir)): sha256(p) for p in paths
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=float) + "\n", encoding="utf-8"
    )
    hashes = {
        str(p.relative_to(output_dir)): sha256(p)
        for p in sorted(output_dir.rglob("*"))
        if p.is_file() and p.name != "SHA256SUMS.txt"
    }
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{d}  {n}\n" for n, d in hashes.items()), encoding="utf-8"
    )
    print(f"A-18 remedy probes written to {output_dir}")
    print(json.dumps({
        "probe_a_median_I_det": probe_a_summary["median_I_deterministic"],
        "probe_a_median_I_stoch_same_denom":
            probe_a_summary["median_I_stochastic_mean_same_denominator"],
        "probe_b_nroy_baseline": probe_b["nroy_baseline"][0]["n_nroy"],
        "probe_b_nroy_with_term": probe_b["nroy_with_term"][0]["n_nroy"],
        "probe_c_identical": {
            k: v["bit_identical_to_production_deterministic"]
            for k, v in probe_c["variants"].items()
        },
    }, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
