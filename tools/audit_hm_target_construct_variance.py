#!/usr/bin/env python3
"""Audit history-matching target constructs and variance budgets without simulation.

The audit is deliberately post-processing only.  It reads completed Wave 1/2
artifacts, verifies the ECDC AIDS workbook extraction, and rescores the existing
candidate means under fixed target-relative discrepancy rules and alternative
target roles.  It never changes the target register or an existing run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_RUNS = (
    ("wave_01", "outputs/calibration/hm_baseline_learning_20260915/wave_01"),
    ("wave_02", "outputs/calibration/hm_focused_continuation_20260915/wave_02"),
)
THRESHOLD = 3.0
THREE_REPLICATE_P95_SAMPLE_VARIANCE_FACTOR = 2.995732273553991


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_wave(label: str, wave_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    evaluated = pd.read_csv(wave_dir / "evaluated.csv")
    targets = pd.read_csv(wave_dir / "per_target_implausibility.csv")
    budget = pd.read_csv(wave_dir / "variance_budget.csv")
    evaluated = pd.concat(
        [pd.Series(label, index=evaluated.index, name="source_wave"), evaluated],
        axis=1,
    )
    return evaluated, targets, budget


def _score(
    candidates: pd.DataFrame,
    target_meta: pd.DataFrame,
    per_run_ensemble_variance: dict[str, float],
    *,
    fraction: float,
    replicates: int,
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for target in target_meta.itertuples(index=False):
        key = str(target.target_key)
        output_column = f"{target.model_output}_{int(target.year)}"
        residual = candidates[output_column].astype(float) - float(target.z)
        v_obs = float(target.v_obs_variance)
        v_ensemble = float(per_run_ensemble_variance[key]) / int(replicates)
        v_disc = (float(fraction) * abs(float(target.z))) ** 2
        total_sigma = float(np.sqrt(v_obs + v_ensemble + v_disc))
        rows.append(
            pd.DataFrame(
                {
                    "source_wave": candidates["source_wave"],
                    "design_id": candidates["design_id"].astype(int),
                    "target_key": key,
                    "series_name": str(target.series_name),
                    "year": int(target.year),
                    "observed": float(target.z),
                    "simulated": candidates[output_column].astype(float),
                    "residual": residual,
                    "v_obs_variance": v_obs,
                    "v_ensemble_variance": v_ensemble,
                    "v_disc_variance": v_disc,
                    "total_sigma": total_sigma,
                    "implausibility": residual.abs() / total_sigma,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def _candidate_summary(scores: pd.DataFrame, scenario: str, included_series: set[str]) -> pd.DataFrame:
    included = scores[scores["series_name"].isin(included_series)].copy()
    grouped = included.groupby(["source_wave", "design_id"], sort=False)
    result = grouped["implausibility"].max().rename("i_max").reset_index()
    binding_idx = grouped["implausibility"].idxmax()
    binding = included.loc[binding_idx, ["source_wave", "design_id", "target_key"]]
    result = result.merge(binding, on=["source_wave", "design_id"], how="left")
    result.insert(0, "scenario", scenario)
    result["nroy"] = result["i_max"] < THRESHOLD
    return result.sort_values(["i_max", "source_wave", "design_id"], ignore_index=True)


def _required_discrepancy(scores: pd.DataFrame, replicates: int) -> pd.DataFrame:
    base_variance = scores["v_obs_variance"] + scores["v_ensemble_variance"]
    needed_variance = np.maximum((scores["residual"].abs() / THRESHOLD) ** 2 - base_variance, 0.0)
    out = scores[
        ["source_wave", "design_id", "target_key", "series_name", "year", "observed", "simulated", "residual"]
    ].copy()
    out["replicates"] = int(replicates)
    out["minimum_disc_sigma_for_i_below_3"] = np.sqrt(needed_variance)
    out["minimum_disc_fraction_of_target"] = np.where(
        out["observed"].abs() > 0,
        out["minimum_disc_sigma_for_i_below_3"] / out["observed"].abs(),
        np.nan,
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/calibration/hm_target_construct_variance_audit_20260915"),
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path("../data/raw/validation/DiseaseData.xlsx"),
    )
    parser.add_argument(
        "--confirmation-dir",
        type=Path,
        default=Path("outputs/calibration/hm_confirmation_wave2_designs_25_342"),
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    loaded = [(label, Path(path).resolve(), *_load_wave(label, Path(path))) for label, path in DEFAULT_RUNS]
    candidates = pd.concat([item[2] for item in loaded], ignore_index=True)

    # The target definitions are identical across these two waves.  Preserve the
    # Wave 2 metadata and explicitly verify that key/z/output mappings agree.
    wave1_targets, wave2_targets = loaded[0][3], loaded[1][3]
    meta_cols = ["target_key", "series_name", "model_output", "year", "z"]
    left = wave1_targets[meta_cols].sort_values("target_key").reset_index(drop=True)
    right = wave2_targets[meta_cols].sort_values("target_key").reset_index(drop=True)
    if not left.equals(right):
        raise RuntimeError("Wave 1 and Wave 2 target definitions differ; combined rescoring is invalid")

    wave2_budget = loaded[1][4].set_index("target_key")
    target_meta = wave2_targets[meta_cols].copy()
    target_meta["v_obs_variance"] = target_meta["target_key"].map(wave2_budget["v_obs_variance"])
    per_run_ensemble = (wave2_budget["v_ensemble_variance"] * 3.0).to_dict()

    # Verify the source workbook row selection independently of the register loader.
    aids_source = pd.read_excel(args.workbook, sheet_name="EDC- AIDS", header=1)
    aids_filter = (
        aids_source["Population"].astype(str).eq("AIDS|Confirmed cases")
        & aids_source["Indicator"].astype(str).eq("Reported cases")
        & aids_source["Unit"].astype(str).eq("N")
    )
    aids_extract = aids_source.loc[aids_filter, ["Time", "NumValue"]].copy()
    aids_extract.columns = ["year", "confirmed_aids_reports"]
    aids_extract.to_csv(output_dir / "ecdc_aids_source_extract.csv", index=False)

    registered_aids = target_meta[target_meta["series_name"].eq("ecdc_aids_confirmed_cases")][
        ["year", "z"]
    ].rename(columns={"z": "registered_target"})
    source_check = registered_aids.merge(aids_extract, on="year", how="left")
    source_check["exact_match"] = source_check["registered_target"].eq(source_check["confirmed_aids_reports"])
    source_check.to_csv(output_dir / "ecdc_aids_register_reconciliation.csv", index=False)

    series_all = set(target_meta["series_name"])
    series_without_aids = series_all - {"ecdc_aids_confirmed_cases"}
    scenarios: list[pd.DataFrame] = []
    score_exports: dict[str, pd.DataFrame] = {}
    for fraction in (0.10, 0.20):
        for replicates in (3, 50):
            label = f"target_relative_{int(fraction * 100)}pct_k{replicates}"
            scored = _score(
                candidates,
                target_meta,
                per_run_ensemble,
                fraction=fraction,
                replicates=replicates,
            )
            score_exports[label] = scored
            scenarios.append(_candidate_summary(scored, f"all_targets_{label}", series_all))
            scenarios.append(
                _candidate_summary(scored, f"aids_held_out_{label}", series_without_aids)
            )
            scenarios.append(
                _candidate_summary(scored, f"aids_only_{label}", {"ecdc_aids_confirmed_cases"})
            )
    candidate_scenarios = pd.concat(scenarios, ignore_index=True)
    candidate_scenarios.to_csv(output_dir / "candidate_scenario_scores.csv", index=False)

    scenario_summary = (
        candidate_scenarios.groupby("scenario", sort=False)
        .agg(
            n_candidates=("design_id", "size"),
            n_nroy=("nroy", "sum"),
            best_i_max=("i_max", "min"),
            median_i_max=("i_max", "median"),
        )
        .reset_index()
    )
    best_rows = candidate_scenarios.groupby("scenario", sort=False).head(1)[
        ["scenario", "source_wave", "design_id", "target_key"]
    ].rename(columns={"target_key": "binding_target_at_best"})
    scenario_summary = scenario_summary.merge(best_rows, on="scenario", how="left")
    scenario_summary.to_csv(output_dir / "scenario_summary.csv", index=False)

    # Cross-validation performance of the best non-AIDS candidate at high precision.
    heldout_rows: list[dict] = []
    for fraction in (0.10, 0.20):
        label = f"target_relative_{int(fraction * 100)}pct_k50"
        no_aids = candidate_scenarios[
            candidate_scenarios["scenario"].eq(f"aids_held_out_{label}")
        ].iloc[0]
        selected = score_exports[label][
            score_exports[label]["source_wave"].eq(no_aids.source_wave)
            & score_exports[label]["design_id"].eq(no_aids.design_id)
            & score_exports[label]["series_name"].eq("ecdc_aids_confirmed_cases")
        ]
        worst = selected.loc[selected["implausibility"].idxmax()]
        heldout_rows.append(
            {
                "discrepancy_fraction": fraction,
                "source_wave": no_aids.source_wave,
                "design_id": int(no_aids.design_id),
                "non_aids_i_max": float(no_aids.i_max),
                "aids_heldout_i_max": float(worst.implausibility),
                "aids_heldout_binding_target": worst.target_key,
            }
        )
    pd.DataFrame(heldout_rows).to_csv(output_dir / "heldout_aids_check.csv", index=False)

    required = _required_discrepancy(score_exports["target_relative_10pct_k50"], replicates=50)
    required.to_csv(output_dir / "minimum_discrepancy_required_k50.csv", index=False)

    # Budget diagnostics: effective target-relative size, wave dependence, and
    # whether A-5 per-run variance covers the empirical within-point variance.
    budget_rows: list[pd.DataFrame] = []
    for label, wave_dir, evaluated, targets, budget in loaded:
        b = budget.merge(targets[["target_key", "series_name", "model_output", "year", "z"]], on="target_key")
        b.insert(0, "source_wave", label)
        b["disc_sigma_as_fraction_of_target"] = b["v_disc_sigma"] / b["z"].abs()
        b["ensemble_sigma_k50"] = np.sqrt(b["v_ensemble_variance"] * 3.0 / 50.0)
        empirical_p95: list[float] = []
        empirical_median: list[float] = []
        for row in b.itertuples(index=False):
            col = f"mcvar__{row.model_output}_{int(row.year)}"
            values = evaluated[col].astype(float).replace([np.inf, -np.inf], np.nan).dropna()
            empirical_median.append(float(values.median()))
            empirical_p95.append(float(values.quantile(0.95)))
        b["empirical_mcvar_median_across_designs"] = empirical_median
        b["empirical_mcvar_p95_across_designs"] = empirical_p95
        b["a5_per_run_variance"] = b["v_ensemble_variance"] * 3.0
        b["empirical_p95_to_a5_ratio"] = b["empirical_mcvar_p95_across_designs"] / b["a5_per_run_variance"]
        budget_rows.append(b)
    budget_audit = pd.concat(budget_rows, ignore_index=True)
    budget_audit.to_csv(output_dir / "variance_budget_audit.csv", index=False)

    wide = budget_audit.pivot(index="target_key", columns="source_wave", values="v_disc_sigma")
    wide["wave2_to_wave1_disc_sigma_ratio"] = wide["wave_02"] / wide["wave_01"]
    wide.reset_index().to_csv(output_dir / "wave_dependence.csv", index=False)

    construct_rows = [
        {
            "series_name": "ecdc_aids_confirmed_cases",
            "observed_construct": "Confirmed AIDS diagnoses/reports assigned to year of diagnosis (surveillance event)",
            "model_construct": "Biological Chronic-to-AIDS stage transitions during the simulated year",
            "population_alignment": "Incomplete: model transition output can include endogenous groups, but exogenous non-MSM diagnoses all enter Chronic and cannot appear as AIDS at diagnosis",
            "timing_alignment": "Incomplete: surveillance date of AIDS diagnosis need not equal biological transition time",
            "observation_uncertainty": "Underivable in workbook; current V_obs is zero",
            "construct_rating": "not exchangeable without a diagnosis/reporting measurement model",
            "recommended_role_now": "cross_validation",
            "route_to_calibration": "Add and validate AIDS-at-diagnosis/reporting mechanism, or obtain a construct-matched observed progression series",
        },
        {
            "series_name": "ecdc_hiv_confirmed_cases",
            "observed_construct": "Confirmed HIV diagnoses/reports assigned to year of diagnosis",
            "model_construct": "Diagnoses generated by the model plus exogenous non-MSM diagnosis input",
            "population_alignment": "Reasonable for 1990-1995 before C-1 series begins, subject to migration and ascertainment limitations",
            "timing_alignment": "Broadly aligned to diagnosis year",
            "observation_uncertainty": "Underivable in workbook; current V_obs is zero",
            "construct_rating": "usable with explicit surveillance uncertainty limitation",
            "recommended_role_now": "calibration_restricted",
            "route_to_calibration": "Retain restricted non-overlap window and declare ascertainment/reporting uncertainty",
        },
        {
            "series_name": "shm_new_diagnoses_msm",
            "observed_construct": "MSM plus transgender HIV diagnoses, delay-adjusted only where published",
            "model_construct": "Endogenous MSM HIV diagnoses",
            "population_alignment": "Close but not exact: observed series includes transgender people and cohort ascertainment",
            "timing_alignment": "Aligned to diagnosis year",
            "observation_uncertainty": "Notification-delay proxy only; ascertainment component missing",
            "construct_rating": "best available direct calibration series",
            "recommended_role_now": "calibration",
            "route_to_calibration": "Retain; document splice and missing ascertainment uncertainty",
        },
    ]
    pd.DataFrame(construct_rows).to_csv(output_dir / "target_construct_audit.csv", index=False)

    ss = scenario_summary.set_index("scenario")
    confirmed_checks: list[dict] = []
    for fraction in (0.10, 0.20):
        path = args.confirmation_dir / f"target_scores_target_relative_{int(fraction * 100)}pct.csv"
        confirmed = pd.read_csv(path)
        confirmed["v_obs_variance"] = confirmed["target_key"].map(wave2_budget["v_obs_variance"])
        confirmed["local_total_sigma"] = np.sqrt(
            confirmed["v_obs_variance"]
            + confirmed["empirical_se_of_mean"] ** 2
            + (fraction * confirmed["observed"].abs()) ** 2
        )
        confirmed["local_variance_implausibility"] = (
            (confirmed["confirmed_mean"] - confirmed["observed"]).abs()
            / confirmed["local_total_sigma"]
        )
        for design_id, group in confirmed.groupby("design_id"):
            non_aids = group[~group["target_key"].str.startswith("ecdc_aids_confirmed_cases@")]
            aids = group[group["target_key"].str.startswith("ecdc_aids_confirmed_cases@")]
            non_aids_worst = non_aids.loc[non_aids["implausibility"].idxmax()]
            aids_worst = aids.loc[aids["implausibility"].idxmax()]
            local_non_aids_worst = non_aids.loc[non_aids["local_variance_implausibility"].idxmax()]
            local_aids_worst = aids.loc[aids["local_variance_implausibility"].idxmax()]
            a5_variance = group["target_key"].map(per_run_ensemble).astype(float)
            empirical_variance = group["empirical_sd_per_run"] ** 2
            confirmed_checks.append(
                {
                    "design_id": int(design_id),
                    "discrepancy_fraction": fraction,
                    "replicates": 50,
                    "non_aids_i_max": float(non_aids_worst.implausibility),
                    "non_aids_binding_target": non_aids_worst.target_key,
                    "non_aids_nroy": bool(non_aids_worst.implausibility < THRESHOLD),
                    "aids_heldout_i_max": float(aids_worst.implausibility),
                    "aids_heldout_binding_target": aids_worst.target_key,
                    "local_variance_non_aids_i_max": float(local_non_aids_worst.local_variance_implausibility),
                    "local_variance_non_aids_binding_target": local_non_aids_worst.target_key,
                    "local_variance_non_aids_nroy": bool(
                        local_non_aids_worst.local_variance_implausibility < THRESHOLD
                    ),
                    "local_variance_aids_i_max": float(local_aids_worst.local_variance_implausibility),
                    "local_variance_aids_binding_target": local_aids_worst.target_key,
                    "median_empirical_to_a5_per_run_variance_ratio": float(
                        np.median(empirical_variance / a5_variance)
                    ),
                    "max_empirical_to_a5_per_run_variance_ratio": float(
                        np.max(empirical_variance / a5_variance)
                    ),
                }
            )
    confirmed_checks_df = pd.DataFrame(confirmed_checks)
    confirmed_checks_df.to_csv(output_dir / "confirmed_non_aids_rescoring.csv", index=False)
    confirmed_25 = confirmed_checks_df[confirmed_checks_df["design_id"].eq(25)].set_index("discrepancy_fraction")
    aids_budget = budget_audit[
        budget_audit["source_wave"].eq("wave_02")
        & budget_audit["series_name"].eq("ecdc_aids_confirmed_cases")
    ]
    all_budget = budget_audit[budget_audit["source_wave"].eq("wave_02")]
    exact_source = bool(source_check["exact_match"].all()) and len(aids_extract) == 28

    lines = [
        "# Target-construct and variance-budget audit",
        "",
        "**Date:** 2026-09-15  ",
        "**Scope:** completed Wave 1 and Wave 2 artifacts only; no new simulations.  ",
        "**Decision status:** audit recommendation, not an automatic target-register change.",
        "",
        "## Bottom line",
        "",
        "The ECDC workbook extraction and register values are arithmetically correct. The mapping is not construct-matched: ECDC counts AIDS diagnoses/reports, while `new_aids_cases` counts biological Chronic-to-AIDS transitions. C-1 makes the mismatch directional because injected non-MSM diagnoses cannot enter as AIDS at diagnosis. The current ECDC AIDS series should therefore be held out for cross-validation unless a diagnosis/reporting measurement model is added.",
        "",
        "The current primary discrepancy rule is unsuitable as the final scientific budget. It makes the tolerance depend on spread in the sampled parameter design. In Wave 2 its one-sigma discrepancy is "
        f"{all_budget['disc_sigma_as_fraction_of_target'].min():.1%} to {all_budget['disc_sigma_as_fraction_of_target'].max():.1%} of the observed count, and it dominates every target budget. It therefore creates a very permissive learning region whose meaning changes with the sampled design.",
        "",
        "Holding AIDS out materially changes the conclusion, but the variance estimator matters. Using the global conservative A-5 variance, Wave 2 design 25 is the sole screened non-AIDS survivor and its 50-seed mean remains NROY at both 10% and 20%. Using variance measured locally from those same 50 seeds, however, design 25 fails at 10% "
        f"(I_max={confirmed_25.loc[0.10,'local_variance_non_aids_i_max']:.3f}) and passes at 20% (I_max={confirmed_25.loc[0.20,'local_variance_non_aids_i_max']:.3f}). A provisional region is demonstrated only under the wider discrepancy or the global-conservative variance policy; neither is yet a final scientific choice.",
        "",
        "## ECDC AIDS mapping",
        "",
        f"- Source reconciliation: {'PASS' if exact_source else 'FAIL'}. The workbook filter returns {len(aids_extract)} annual values for 1990-2017, and all six registered anchors match exactly.",
        "- Observed event: a confirmed AIDS surveillance diagnosis/report, generally presented by date of diagnosis.",
        "- Model event: a simulated biological transition from Chronic to AIDS. The output does not represent notification, reporting delay, ascertainment, migration history, or concurrent AIDS at HIV diagnosis.",
        "- C-1 consequence: observed non-MSM diagnoses are injected into Chronic, so they contribute zero AIDS-at-diagnosis events to `new_aids_cases`.",
        "- Recommendation: move ECDC AIDS from direct calibration to cross-validation for the next learning run. This is based on construct validity, not on whether the target passes.",
        "",
        "## Existing-candidate rescoring",
        "",
        "| Target treatment | Discrepancy | k | Best I_max | NROY / 1,974 | Best vector |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for scenario in (
        "all_targets_target_relative_10pct_k50",
        "aids_held_out_target_relative_10pct_k50",
        "all_targets_target_relative_20pct_k50",
        "aids_held_out_target_relative_20pct_k50",
        "aids_only_target_relative_10pct_k50",
        "aids_only_target_relative_20pct_k50",
    ):
        row = ss.loc[scenario]
        treatment = "All 19" if scenario.startswith("all_targets") else ("13; AIDS held out" if scenario.startswith("aids_held_out") else "AIDS only")
        fraction = "10%" if "10pct" in scenario else "20%"
        lines.append(
            f"| {treatment} | {fraction} | 50 | {row.best_i_max:.3f} | {int(row.n_nroy)} | {row.source_wave} / {int(row.design_id)} |"
        )
    lines.extend(
        [
            "",
            "The k=50 entries above rescore three-replicate candidate means with a tighter denominator; they are a screen, not 50-replicate estimates. In the separate 50-seed run, design 25's non-AIDS I_max under global A-5 is "
            f"{confirmed_25.loc[0.10,'non_aids_i_max']:.3f} at 10% and {confirmed_25.loc[0.20,'non_aids_i_max']:.3f} at 20%. Replacing global A-5 with its empirical local standard errors gives {confirmed_25.loc[0.10,'local_variance_non_aids_i_max']:.3f} and {confirmed_25.loc[0.20,'local_variance_non_aids_i_max']:.3f}. Its held-out AIDS I_max under global A-5 is {confirmed_25.loc[0.10,'aids_heldout_i_max']:.3f} and {confirmed_25.loc[0.20,'aids_heldout_i_max']:.3f}. This is validation evidence of the construct mismatch, not a reason to widen the calibration denominator until it passes.",
            "",
            "## Variance budget findings",
            "",
            f"- V_obs is zero for {int((all_budget['v_obs_variance'] == 0).sum())} of 19 targets, including all {len(aids_budget)} AIDS anchors and all six early ECDC HIV anchors. Zero represents missing evidence, not perfect measurement.",
            f"- The output-variance V_disc is the largest term for {int((all_budget['dominant_term'] == 'v_disc').sum())} of 19 Wave 2 targets.",
            f"- Across targets, changing from k=3 to k=50 reduces the A-5 Monte Carlo standard error by a factor of {np.sqrt(50/3):.2f}. It should improve precision; it should not change structural or observation uncertainty.",
            f"- The Wave 2 to Wave 1 output-variance discrepancy sigma ratio ranges from {wide['wave2_to_wave1_disc_sigma_ratio'].min():.3f} to {wide['wave2_to_wave1_disc_sigma_ratio'].max():.3f}. A scientific discrepancy term should not move merely because the next design samples a narrower region.",
            f"- Three-replicate sample variances are intrinsically noisy: even under constant true variance, their theoretical p95 is {THREE_REPLICATE_P95_SAMPLE_VARIANCE_FACTOR:.2f} times the true variance. After using that as the comparison threshold, the Wave 2 empirical within-point p95 still exceeds the A-5 expectation for {int((all_budget['empirical_p95_to_a5_ratio'] > THREE_REPLICATE_P95_SAMPLE_VARIANCE_FACTOR).sum())} of 19 targets. This flags possible input-dependent variance, especially in early ECDC HIV years, but does not by itself invalidate A-5.",
            f"- At confirmed design 25, the local per-run variance is below A-5 for every target (maximum local/A-5 ratio {confirmed_25.loc[0.10,'max_empirical_to_a5_per_run_variance_ratio']:.3f}). The global A-5 rule is therefore conservative there, but materially so: it is what changes the non-AIDS 10% decision from fail to pass.",
            "",
            "## Decision for the next run",
            "",
            "1. Hold `ecdc_aids_confirmed_cases` out as cross-validation. Do not add an arbitrary AIDS-specific variance just to retain it as a calibration target.",
            "2. Replace output-variance discrepancy as the next learning run's primary rule with a prespecified, target-scale rule. Ten percent primary and 20% sensitivity are transparent starting points, not empirically established truths and must not be selected based on which one passes.",
            "3. Keep V_obs, V_ensemble, and V_disc separate. Treat missing V_obs as an unresolved limitation; do not imply that zero means exact surveillance.",
            "4. Resolve the V_ensemble policy before the next wave. The defensible direction is input-dependent variance for promoted candidates, with A-5 retained as a conservative screening sensitivity rather than the sole final denominator.",
            "5. After that decision, a focused replenishment run around design 25 is justified. Use k=3 only as a cheap first stage, promote near-NROY candidates to k=10, and confirm survivors at k=50 before ensemble freezing.",
            "",
            "## Files",
            "",
            "- `target_construct_audit.csv`: construct-by-construct decision record.",
            "- `ecdc_aids_source_extract.csv` and `ecdc_aids_register_reconciliation.csv`: workbook provenance check.",
            "- `variance_budget_audit.csv` and `wave_dependence.csv`: component sizes and design dependence.",
            "- `scenario_summary.csv` and `candidate_scenario_scores.csv`: fixed-rule rescoring of all existing candidates.",
            "- `heldout_aids_check.csv`: AIDS validation of the best non-AIDS vector.",
            "- `confirmed_non_aids_rescoring.csv`: direct non-AIDS/AIDS split of the completed 50-seed confirmation under both global A-5 and local empirical variance.",
            "- `minimum_discrepancy_required_k50.csv`: diagnostic tolerance required by each candidate-target pair; not a fitted recommendation.",
            "",
            "## External construct references",
            "",
            "- ECDC/WHO, *HIV/AIDS surveillance in Europe 2022 – 2021 data*, Annex 1: https://www.ecdc.europa.eu/sites/default/files/documents/2022-Annual_HIV_Report_final.pdf",
            "- ECDC/WHO, *HIV/AIDS surveillance in Europe 2014*: https://www.ecdc.europa.eu/sites/default/files/media/en/publications/Publications/hiv-aids-surveillance-in-Europe-2014.pdf",
        ]
    )
    (output_dir / "AUDIT_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    inputs = []
    for _, wave_dir, *_ in loaded:
        for name in ("evaluated.csv", "per_target_implausibility.csv", "variance_budget.csv"):
            path = wave_dir / name
            inputs.append({"path": str(path), "sha256": _sha256(path)})
    inputs.append({"path": str(args.workbook.resolve()), "sha256": _sha256(args.workbook.resolve())})
    for name in ("target_scores_target_relative_10pct.csv", "target_scores_target_relative_20pct.csv"):
        path = args.confirmation_dir.resolve() / name
        inputs.append({"path": str(path), "sha256": _sha256(path)})
    manifest = {
        "audit": "target_construct_and_variance_budget",
        "created": "2026-09-15",
        "new_simulations": 0,
        "candidate_count": int(len(candidates)),
        "threshold": THRESHOLD,
        "inputs": inputs,
        "outputs": sorted(path.name for path in output_dir.iterdir() if path.is_file()),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "candidates": len(candidates), "new_simulations": 0}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
