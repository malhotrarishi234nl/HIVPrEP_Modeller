"""Phase 1 of the interim abstract evidence package.

Brief: ``TASK_BRIEF_ABSTRACT_EVIDENCE_PACKAGE_2026-09-23.md``.

This script is read-only on every source artifact.  It runs no simulation.  It

1. reproduces the historical controls (brief section 5.1) and the completed
   matched substitution HTA controls (section 5.2) from the saved files;
2. completes phases 4-6 of the stage-diagnosis whole-model review
   (mortality, AIDS burden, normalised rates, trajectory shape, non-MSM
   effects, external comparisons) from the saved confirmation outputs;
3. freezes provenance into ``SOURCE_REGISTER.csv``;
4. writes the exact field list the symmetric no-ISR reweighting requires;
5. writes the frozen design JSON for the proposed matched deployment run.

Everything it writes lands under ``outputs/abstract_evidence_20260923/``.
No source directory is moved, renamed or modified.

    .venv/bin/python tools/build_abstract_evidence_phase1.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REVIEW = ROOT / "outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922"
ORCH = ROOT / "outputs/calibration/diagnosis_triangulation_orchestration_20260922"
MATCHED = REVIEW / "matched_hta_20260922"
DEFAULT_OUT = ROOT / "outputs/abstract_evidence_20260923"

CANDIDATE = "D_1788_v109"
LEGACY = "D_1788_v000"
MORTALITY_WINDOW = tuple(range(2015, 2025))
ZIN_WTP_TIERS = (20_000.0, 50_000.0, 80_000.0)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def paired(values: Sequence[float]) -> dict[str, float]:
    """Mean, SD and a two-sided 95% Student-t interval for a paired difference."""
    array = np.asarray([float(v) for v in values], dtype=float)
    n = int(array.size)
    mean = float(array.mean())
    sd = float(array.std(ddof=1)) if n > 1 else math.nan
    se = sd / math.sqrt(n) if n > 1 else math.nan
    margin = float(student_t.ppf(0.975, n - 1) * se) if n > 1 else math.nan
    low, high = (mean - margin, mean + margin) if n > 1 else (math.nan, math.nan)
    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "ci95_low": low,
        "ci95_high": high,
        "ci_excludes_zero": bool(n > 1 and (low > 0 or high < 0)),
    }


def control(
    rows: list[dict[str, Any]],
    section: str,
    name: str,
    expected: Any,
    observed: Any,
    *,
    tolerance: float | None = None,
    source: str = "",
) -> None:
    if isinstance(expected, (int, float)) and isinstance(observed, (int, float)):
        difference = float(observed) - float(expected)
        tol = 5e-3 if tolerance is None else tolerance
        passes = abs(difference) <= tol
    else:
        difference = math.nan
        passes = str(expected) == str(observed)
    rows.append(
        {
            "section": section,
            "control": name,
            "expected": expected,
            "observed": observed,
            "difference": difference,
            "tolerance": tolerance if tolerance is not None else (5e-3 if difference == difference else ""),
            "status": "PASS" if passes else "FAIL",
            "source": source,
        }
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_historical() -> dict[str, Any]:
    seed_long = pd.read_csv(REVIEW / "seed_level_long_1996_2025.csv.gz")
    return {
        "seed_long": seed_long,
        "wide": seed_long.pivot_table(
            index=["seed", "year"], columns=["tuple_id", "series"], values="value"
        ),
        "scalars": pd.read_csv(REVIEW / "seed_level_scalars.csv"),
        "annual": pd.read_csv(REVIEW / "candidate_vs_legacy_annual.csv"),
        "implausibility": pd.read_csv(REVIEW / "target_implausibility_table.csv"),
        "external": pd.read_csv(REVIEW / "external_evidence_comparison.csv"),
        "integrity": json.loads((REVIEW / "integrity_checks.json").read_text()),
        "decomposition": pd.read_csv(ORCH / "stage_diagnosis_decomposition.csv"),
        "identifiability": json.loads((ORCH / "identifiability.json").read_text()),
    }


def load_matched() -> dict[str, Any]:
    return {
        "manifest": json.loads((MATCHED / "manifest.json").read_text()),
        "per_seed": pd.read_csv(MATCHED / "per_seed_economics.csv"),
        "decision": pd.read_csv(MATCHED / "decision_summary.csv"),
        "pooled": pd.read_csv(MATCHED / "pooled_price_thresholds.csv"),
        "paired": pd.read_csv(MATCHED / "paired_structure_difference_summary.csv"),
    }


# ---------------------------------------------------------------------------
# 1. Control reproduction
# ---------------------------------------------------------------------------
def reproduce_historical_controls(hist: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    src = "outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/"
    scalars, imp = hist["scalars"], hist["implausibility"]

    for arm, label in ((CANDIDATE, "late diagnosis"), (LEGACY, "legacy")):
        seeds = scalars[scalars.tuple_id == arm]["seed"]
        control(rows, "5.1", f"{label}: number of confirmation seeds", 30, int(seeds.nunique()),
                tolerance=0, source=src + "seed_level_scalars.csv")
    identical = set(scalars[scalars.tuple_id == CANDIDATE]["seed"]) == set(
        scalars[scalars.tuple_id == LEGACY]["seed"]
    )
    control(rows, "5.1", "identical confirmation seed set", True, identical,
            source=src + "seed_level_scalars.csv")

    cand = imp[imp.tuple_id == CANDIDATE].set_index("target")
    leg = imp[imp.tuple_id == LEGACY].set_index("target")
    control(rows, "5.1", "legacy I_max", 1.919134,
            float(leg["primary_implausibility"].max()), tolerance=1e-6,
            source=src + "target_implausibility_table.csv")
    control(rows, "5.1", "late diagnosis I_max", 2.634565,
            float(cand["primary_implausibility"].max()), tolerance=1e-6,
            source=src + "target_implausibility_table.csv")

    targets = sorted(set(cand.index) & set(leg.index))
    cand_better = sum(
        1 for t in targets
        if cand.loc[t, "primary_implausibility"] < leg.loc[t, "primary_implausibility"]
    )
    control(rows, "5.1", "targets on which late diagnosis is better (of 13)", 6,
            cand_better, tolerance=0, source=src + "target_implausibility_table.csv")
    control(rows, "5.1", "targets on which legacy is better (of 13)", 7,
            len(targets) - cand_better, tolerance=0,
            source=src + "target_implausibility_table.csv")
    control(rows, "5.1", "mean primary implausibility, late diagnosis", 1.109,
            float(cand["primary_implausibility"].mean()), tolerance=5e-4,
            source=src + "target_implausibility_table.csv")
    control(rows, "5.1", "mean primary implausibility, legacy", 1.096,
            float(leg["primary_implausibility"].mean()), tolerance=5e-4,
            source=src + "target_implausibility_table.csv")
    control(rows, "5.1", "all 13 primary targets pass I <= 3, both structures", True,
            bool(cand["primary_pass_I_le_3"].all() and leg["primary_pass_I_le_3"].all()),
            source=src + "target_implausibility_table.csv")

    def scalar_mean(arm: str, column: str) -> float:
        return float(scalars[scalars.tuple_id == arm][column].mean())

    expectations = [
        ("2024 MSM diagnoses, late diagnosis", CANDIDATE, "dx2024", 305.67),
        ("2024 MSM diagnoses, legacy", LEGACY, "dx2024", 195.47),
        ("2024 MSM infections, late diagnosis", CANDIDATE, "msm_new_infections_2024", 292.97),
        ("2024 MSM infections, legacy", LEGACY, "msm_new_infections_2024", 184.97),
        ("end-2024 undiagnosed MSM, late diagnosis", CANDIDATE, "msm_undiagnosed_end2024", 915.93),
        ("end-2024 undiagnosed MSM, legacy", LEGACY, "msm_undiagnosed_end2024", 424.97),
        ("MSM HIV deaths 2015-2024, late diagnosis", CANDIDATE, "msm_hiv_deaths_2015_2024", 1202.73),
        ("MSM HIV deaths 2015-2024, legacy", LEGACY, "msm_hiv_deaths_2015_2024", 991.00),
    ]
    for name, arm, column, expected in expectations:
        control(rows, "5.1", name, expected, scalar_mean(arm, column), tolerance=5e-3,
                source=src + "seed_level_scalars.csv")

    control(rows, "5.1", "observed 2024 MSM diagnoses (target)", 318.0,
            float(imp[(imp.tuple_id == CANDIDATE) & (imp.target == "shm_new_diagnoses_msm@2024")]
                  ["observed"].iloc[0]), tolerance=0,
            source=src + "target_implausibility_table.csv")

    reservoir = scalars[scalars.tuple_id == CANDIDATE]["msm_undiagnosed_end2024"]
    control(rows, "5.1", "late-diagnosis reservoir overshoot above 900", 15.93,
            float(reservoir.mean() - 900.0), tolerance=5e-3,
            source=src + "seed_level_scalars.csv")
    control(rows, "5.1", "late-diagnosis reservoir overshoot, per cent of 900", 1.77,
            float((reservoir.mean() - 900.0) / 900.0 * 100.0), tolerance=5e-3,
            source=src + "seed_level_scalars.csv")
    control(rows, "5.1", "late-diagnosis seeds inside Tier-2 690-900 (of 30)", 15,
            int(((reservoir >= 690) & (reservoir <= 900)).sum()), tolerance=0,
            source=src + "seed_level_scalars.csv")

    external = hist["external"].set_index("check_id")
    control(rows, "5.1", "Tier-2 interval for 2024 MSM infections (lower)", 250.0,
            float(external.loc["E1", "lower"]), tolerance=0,
            source=src + "external_evidence_comparison.csv")
    control(rows, "5.1", "Tier-2 interval for 2024 MSM infections (upper)", 365.0,
            float(external.loc["E1", "upper"]), tolerance=0,
            source=src + "external_evidence_comparison.csv")
    control(rows, "5.1", "Tier-2 interval for end-2024 undiagnosed MSM (lower)", 690.0,
            float(external.loc["E4", "lower"]), tolerance=0,
            source=src + "external_evidence_comparison.csv")
    control(rows, "5.1", "Tier-2 interval for end-2024 undiagnosed MSM (upper)", 900.0,
            float(external.loc["E4", "upper"]), tolerance=0,
            source=src + "external_evidence_comparison.csv")
    return rows


def reproduce_matched_controls(matched: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    src = ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/"
           "matched_hta_20260922/")
    manifest, per_seed = matched["manifest"], matched["per_seed"]

    hashes_pass = sum(
        sha256_file(MATCHED / name) == expected
        for name, expected in manifest["output_sha256"].items()
    )
    control(rows, "5.2", "manifest output hashes that pass", 7, int(hashes_pass),
            tolerance=0, source=src + "manifest.json")
    control(rows, "5.2", "matched-HTA config hash matches configs/hta_runtime_config.json",
            manifest["hta_config_sha256"],
            sha256_file(ROOT / "configs/hta_runtime_config.json"), source=src + "manifest.json")
    control(rows, "5.2", "matched-HTA costing hash matches configs/hta_costing_config.json",
            manifest["costing_config_sha256"],
            sha256_file(ROOT / "configs/hta_costing_config.json"), source=src + "manifest.json")

    control(rows, "5.2", "unique structure-seed-scenario rows", 180,
            int(len(per_seed.groupby(["structure", "seed", "scenario"]))), tolerance=0,
            source=src + "per_seed_economics.csv")
    control(rows, "5.2", "stochastic seeds", "42-56",
            f"{int(per_seed.seed.min())}-{int(per_seed.seed.max())}",
            source=src + "per_seed_economics.csv")
    control(rows, "5.2", "seeds per structure", 15, int(per_seed.seed.nunique()),
            tolerance=0, source=src + "per_seed_economics.csv")
    control(rows, "5.2", "configured annual lenacapavir price", 41110.44,
            float(per_seed.base_annual_lena_price.dropna().unique()[0]), tolerance=5e-3,
            source=src + "per_seed_economics.csv")

    worst = 0.0
    for wtp in ZIN_WTP_TIERS:
        slug = f"{int(wtp / 1000)}k"
        worst = max(
            worst,
            float((per_seed[f"nmb_{slug}"] - (wtp * per_seed.delta_qaly - per_seed.delta_cost))
                  .abs().max()),
        )
    scale = float(per_seed[["nmb_20k", "nmb_50k", "nmb_80k"]].abs().to_numpy().max())
    control(rows, "5.2", "NMB identity: max absolute error relative to NMB scale", 0.0,
            worst / scale, tolerance=1e-12, source=src + "per_seed_economics.csv")

    substitution = per_seed[per_seed.scenario != "base_case_context"]
    positive = float((substitution.nmb_80k > 0).mean())
    control(rows, "5.2", "P(NMB>0) at EUR 80,000/QALY, all substitution levels, both structures",
            0.0, positive, tolerance=0, source=src + "per_seed_economics.csv")

    decision = matched["decision"].set_index(["structure", "scenario"])
    pooled = matched["pooled"]
    prefix = "scenario_a_lena_substitution__switch_"
    headline = [
        ("legacy", 10, "mean_delta_cost", 237.52e6, 5e3),
        ("legacy", 10, "mean_delta_qaly", 81.79, 5e-3),
        ("legacy", 10, "mean_infections_averted", 10.47, 5e-3),
        ("legacy", 10, "mean_nmb_80k", -230.98e6, 5e3),
        ("candidate", 10, "mean_delta_cost", 230.61e6, 5e3),
        ("candidate", 10, "mean_delta_qaly", 125.74, 5e-3),
        ("candidate", 10, "mean_infections_averted", 18.53, 5e-3),
        ("candidate", 10, "mean_nmb_80k", -220.55e6, 5e3),
        ("legacy", 25, "mean_delta_cost", 617.67e6, 5e3),
        ("legacy", 25, "mean_delta_qaly", 56.45, 5e-3),
        ("legacy", 25, "mean_infections_averted", 20.00, 5e-3),
        ("legacy", 25, "mean_nmb_80k", -613.16e6, 5e3),
        ("candidate", 25, "mean_delta_cost", 602.92e6, 5e3),
        ("candidate", 25, "mean_delta_qaly", 105.17, 5e-3),
        ("candidate", 25, "mean_infections_averted", 45.27, 5e-3),
        ("candidate", 25, "mean_nmb_80k", -594.51e6, 5e3),
        ("legacy", 100, "mean_delta_cost", 2578.83e6, 5e3),
        ("legacy", 100, "mean_delta_qaly", -785.35, 5e-3),
        ("legacy", 100, "mean_infections_averted", 99.20, 5e-3),
        ("legacy", 100, "mean_nmb_80k", -2641.66e6, 5e3),
        ("candidate", 100, "mean_delta_cost", 2520.57e6, 5e3),
        ("candidate", 100, "mean_delta_qaly", -563.95, 5e-3),
        ("candidate", 100, "mean_infections_averted", 195.07, 5e-3),
        ("candidate", 100, "mean_nmb_80k", -2565.68e6, 5e3),
    ]
    for structure, switch, column, expected, tol in headline:
        observed = float(decision.loc[(structure, f"{prefix}{switch}"), column])
        control(rows, "5.2", f"{structure} {switch}% substitution: {column}", expected,
                observed, tolerance=tol, source=src + "decision_summary.csv")

    prices = [
        ("legacy", 10, 2231.0), ("candidate", 10, 3035.0),
        ("legacy", 25, 1406.0), ("candidate", 25, 1797.0),
        ("legacy", 100, 178.0), ("candidate", 100, 522.0),
    ]
    for structure, switch, expected in prices:
        match = pooled[(pooled.structure == structure)
                       & (pooled.scenario == f"{prefix}{switch}")
                       & (pooled.wtp == 80_000.0)]
        control(rows, "5.2",
                f"{structure} {switch}% substitution: pooled NMB-zero price at EUR 80k/QALY",
                expected, float(match.nmb_zero_price.iloc[0]), tolerance=0.5,
                source=src + "pooled_price_thresholds.csv")

    legacy25 = pooled[(pooled.structure == "legacy")
                      & (pooled.scenario == f"{prefix}25") & (pooled.wtp == 80_000.0)]
    control(rows, "5.2", "legacy 25% substitution: mean QALY CI crosses zero", True,
            bool(legacy25.mean_qaly_ci_crosses_zero.iloc[0]),
            source=src + "pooled_price_thresholds.csv")

    paired_df = matched["paired"].set_index(["scenario", "metric"])
    for metric, expected, tol in (
        ("delta_cost", -58.26e6, 5e3),
        ("delta_qaly", 221.40, 5e-3),
        ("infections_averted", 95.87, 5e-3),
        ("nmb_80k", 75.98e6, 5e3),
    ):
        control(rows, "5.2", f"candidate minus legacy at 100% substitution: {metric}",
                expected, float(paired_df.loc[(f"{prefix}100", metric), "mean"]),
                tolerance=tol, source=src + "paired_structure_difference_summary.csv")
    return rows


# ---------------------------------------------------------------------------
# 2. Whole-model review, phases 4-6
# ---------------------------------------------------------------------------
def _cumulative(wide: pd.DataFrame, arm: str, series: str,
                years: Iterable[int] = MORTALITY_WINDOW) -> pd.Series:
    return wide[(arm, series)].unstack("year")[list(years)].sum(axis=1)


def mortality_decomposition(wide: pd.DataFrame) -> pd.DataFrame:
    states = (
        "undiagnosed_off_art",
        "diagnosed_off_art",
        "diagnosed_on_art",
        "undiagnosed_on_art",
    )
    records: list[dict[str, Any]] = []
    for population in ("msm", "non_msm", "total"):
        total_diff = _cumulative(wide, CANDIDATE, f"hiv_deaths_{population}") - _cumulative(
            wide, LEGACY, f"hiv_deaths_{population}"
        )
        for state in states + ("all_states",):
            series = (
                f"hiv_deaths_{population}"
                if state == "all_states"
                else f"hiv_deaths_{population}_{state}"
            )
            cand = _cumulative(wide, CANDIDATE, series)
            leg = _cumulative(wide, LEGACY, series)
            stats = paired((cand - leg).tolist())
            share = (
                float(stats["mean"] / total_diff.mean())
                if abs(total_diff.mean()) > 1e-12 else math.nan
            )
            records.append(
                {
                    "window": "2015-2024",
                    "population": population,
                    "diagnosis_art_state": state,
                    "late_diagnosis_cumulative_deaths": float(cand.mean()),
                    "legacy_cumulative_deaths": float(leg.mean()),
                    "paired_difference_mean": stats["mean"],
                    "paired_difference_sd": stats["sd"],
                    "paired_ci95_low": stats["ci95_low"],
                    "paired_ci95_high": stats["ci95_high"],
                    "ci_excludes_zero": stats["ci_excludes_zero"],
                    "share_of_population_total_change": share,
                    "n_pairs": stats["n"],
                }
            )
    # By year, MSM only.
    for year in MORTALITY_WINDOW:
        cand = _cumulative(wide, CANDIDATE, "hiv_deaths_msm", [year])
        leg = _cumulative(wide, LEGACY, "hiv_deaths_msm", [year])
        stats = paired((cand - leg).tolist())
        records.append(
            {
                "window": str(year),
                "population": "msm",
                "diagnosis_art_state": "all_states",
                "late_diagnosis_cumulative_deaths": float(cand.mean()),
                "legacy_cumulative_deaths": float(leg.mean()),
                "paired_difference_mean": stats["mean"],
                "paired_difference_sd": stats["sd"],
                "paired_ci95_low": stats["ci95_low"],
                "paired_ci95_high": stats["ci95_high"],
                "ci_excludes_zero": stats["ci_excludes_zero"],
                "share_of_population_total_change": math.nan,
                "n_pairs": stats["n"],
            }
        )
    return pd.DataFrame(records)


def mortality_shift_share(wide: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    """Split the MSM death-rate change into stratum mix and within-stratum rates."""
    strata = {
        "undiagnosed_off_art": (
            "hiv_deaths_msm_undiagnosed_off_art", "msm_undiagnosed_person_years_approx"),
        "diagnosed_off_art": (
            "hiv_deaths_msm_diagnosed_off_art", "msm_diagnosed_off_art_person_years_approx"),
        "on_art": ("hiv_deaths_msm_diagnosed_on_art", "msm_on_art_person_years_approx"),
    }
    plhiv = {arm: _cumulative(wide, arm, "msm_plhiv_person_years_approx")
             for arm in (CANDIDATE, LEGACY)}
    share: dict[tuple[str, str], pd.Series] = {}
    rate: dict[tuple[str, str], pd.Series] = {}
    for key, (deaths, person_years) in strata.items():
        for arm in (CANDIDATE, LEGACY):
            share[(arm, key)] = _cumulative(wide, arm, person_years) / plhiv[arm]
            rate[(arm, key)] = (
                _cumulative(wide, arm, deaths) / _cumulative(wide, arm, person_years) * 1000.0
            )
    observed = {arm: _cumulative(wide, arm, "hiv_deaths_msm") / plhiv[arm] * 1000.0
                for arm in (CANDIDATE, LEGACY)}
    reconstructed = {arm: sum(share[(arm, k)] * rate[(arm, k)] for k in strata)
                     for arm in (CANDIDATE, LEGACY)}
    closure = max(float((reconstructed[a] - observed[a]).abs().max())
                  for a in (CANDIDATE, LEGACY))
    composition = sum((share[(CANDIDATE, k)] - share[(LEGACY, k)]) * rate[(LEGACY, k)]
                      for k in strata)
    within = sum(share[(CANDIDATE, k)] * (rate[(CANDIDATE, k)] - rate[(LEGACY, k)])
                 for k in strata)

    records: list[dict[str, Any]] = []
    total = paired((observed[CANDIDATE] - observed[LEGACY]).tolist())
    records.append({"component": "total change in MSM deaths per 1,000 PLHIV person-years",
                    "legacy_value": float(observed[LEGACY].mean()),
                    "late_diagnosis_value": float(observed[CANDIDATE].mean()), **total})
    for name, values in (("composition: stratum mix", composition),
                         ("within-stratum death rates", within)):
        stats = paired(values.tolist())
        records.append({"component": name, "legacy_value": math.nan,
                        "late_diagnosis_value": math.nan, **stats})
    for key in strata:
        s = paired((share[(CANDIDATE, key)] - share[(LEGACY, key)]).tolist())
        r = paired((rate[(CANDIDATE, key)] - rate[(LEGACY, key)]).tolist())
        records.append({"component": f"share of PLHIV person-time: {key}",
                        "legacy_value": float(share[(LEGACY, key)].mean()),
                        "late_diagnosis_value": float(share[(CANDIDATE, key)].mean()), **s})
        records.append({"component": f"deaths per 1,000 person-years: {key}",
                        "legacy_value": float(rate[(LEGACY, key)].mean()),
                        "late_diagnosis_value": float(rate[(CANDIDATE, key)].mean()), **r})
    aids_share = {arm: _cumulative(wide, arm, "msm_aids_stage_person_years_approx") / plhiv[arm]
                  for arm in (CANDIDATE, LEGACY)}
    stats = paired((aids_share[CANDIDATE] - aids_share[LEGACY]).tolist())
    records.append({"component": "AIDS-stage share of MSM PLHIV person-time",
                    "legacy_value": float(aids_share[LEGACY].mean()),
                    "late_diagnosis_value": float(aids_share[CANDIDATE].mean()), **stats})
    frame = pd.DataFrame(records)
    frame.insert(0, "window", "2015-2024")
    facts = {
        "shift_share_closure_max_abs": closure,
        "rate_total_change": total["mean"],
        "rate_composition_share": float(
            paired(composition.tolist())["mean"] / total["mean"]),
        "rate_within_share": float(paired(within.tolist())["mean"] / total["mean"]),
        "legacy_rate": float(observed[LEGACY].mean()),
        "candidate_rate": float(observed[CANDIDATE].mean()),
    }
    return frame, facts


def aids_burden_decomposition(wide: pd.DataFrame) -> pd.DataFrame:
    flows = (
        "aids_progression_undiagnosed_msm",
        "aids_progression_diagnosed_off_art_msm",
        "aids_progression_diagnosed_on_art_msm",
        "msm_dx_in_aids_stage",
        "msm_dx_symptom_attributed",
        "aids_proxy_msm",
        "aids_proxy_msm_component_dx_in_aids_stage",
        "aids_proxy_msm_component_progression_while_diagnosed",
        "aids_progression_undiagnosed_non_msm",
        "non_msm_dx_in_aids_stage",
        "non_msm_dx_symptom_attributed",
        "art_initiations_total",
        "hiv_deaths_in_aids_stage_total",
    )
    stocks = (
        "msm_aids_stage_end",
        "msm_undiagnosed_aids_end",
        "msm_diagnosed_aids_stage_end",
        "msm_undiagnosed_chronic_end",
        "msm_undiagnosed_acute_end",
    )
    records: list[dict[str, Any]] = []
    for series in flows:
        cand = _cumulative(wide, CANDIDATE, series)
        leg = _cumulative(wide, LEGACY, series)
        stats = paired((cand - leg).tolist())
        records.append({"measure": series, "kind": "cumulative flow 2015-2024",
                        "legacy": float(leg.mean()), "late_diagnosis": float(cand.mean()),
                        **stats})
    for series in stocks:
        cand = wide[(CANDIDATE, series)].unstack("year")[2024]
        leg = wide[(LEGACY, series)].unstack("year")[2024]
        stats = paired((cand - leg).tolist())
        records.append({"measure": series, "kind": "stock at end-2024",
                        "legacy": float(leg.mean()), "late_diagnosis": float(cand.mean()),
                        **stats})
    return pd.DataFrame(records)


def normalized_rates(wide: pd.DataFrame) -> pd.DataFrame:
    definitions = [
        ("MSM HIV deaths per 1,000 PLHIV person-years",
         "hiv_deaths_msm", "msm_plhiv_person_years_approx", 1000.0),
        ("MSM undiagnosed off-ART deaths per 1,000 undiagnosed person-years",
         "hiv_deaths_msm_undiagnosed_off_art", "msm_undiagnosed_person_years_approx", 1000.0),
        ("MSM on-ART deaths per 1,000 on-ART person-years",
         "hiv_deaths_msm_diagnosed_on_art", "msm_on_art_person_years_approx", 1000.0),
        ("MSM AIDS progression while undiagnosed per 1,000 undiagnosed person-years",
         "aids_progression_undiagnosed_msm", "msm_undiagnosed_person_years_approx", 1000.0),
        ("MSM AIDS-stage diagnoses as a fraction of MSM diagnoses",
         "msm_dx_in_aids_stage", "new_diagnoses_msm", 1.0),
        ("MSM symptom-attributed diagnoses as a fraction of MSM diagnoses",
         "msm_dx_symptom_attributed", "new_diagnoses_msm", 1.0),
    ]
    records: list[dict[str, Any]] = []
    for label, numerator, denominator, scale in definitions:
        values = {}
        for arm in (CANDIDATE, LEGACY):
            values[arm] = (_cumulative(wide, arm, numerator)
                           / _cumulative(wide, arm, denominator) * scale)
        stats = paired((values[CANDIDATE] - values[LEGACY]).tolist())
        records.append({"window": "2015-2024", "measure": label,
                        "numerator": numerator, "denominator": denominator,
                        "legacy": float(values[LEGACY].mean()),
                        "late_diagnosis": float(values[CANDIDATE].mean()), **stats})
    # End-2024 coverage ratios read directly.
    for label, series in (
        ("ART coverage among diagnosed MSM, end-2024", "msm_art_coverage_of_diagnosed_end"),
        ("Undiagnosed fraction of MSM PLHIV, end-2024", "msm_undiagnosed_fraction_end"),
    ):
        cand = wide[(CANDIDATE, series)].unstack("year")[2024]
        leg = wide[(LEGACY, series)].unstack("year")[2024]
        stats = paired((cand - leg).tolist())
        records.append({"window": "end-2024", "measure": label, "numerator": series,
                        "denominator": "", "legacy": float(leg.mean()),
                        "late_diagnosis": float(cand.mean()), **stats})
    return pd.DataFrame(records)


def trajectory_divergence(annual: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    series_of_interest = (
        "plhiv_total_end", "plhiv_msm_end", "plhiv_non_msm_end", "msm_undiagnosed_end",
        "new_infections_msm", "new_diagnoses_msm", "msm_on_art_end", "msm_diagnosed_end",
        "hiv_deaths_msm", "msm_aids_stage_end", "msm_dx_in_aids_stage",
    )
    records: list[dict[str, Any]] = []
    anchors = (2004, 2019)
    for series in series_of_interest:
        group = annual[annual.series == series].sort_values("year").set_index("year")
        significant = group[group.ci_excludes_zero == True]  # noqa: E712
        first = int(significant.index[0]) if len(significant) else None
        sign_changes = int(
            (np.sign(group.paired_diff_mean).diff().fillna(0) != 0).sum()
        )
        step = group.paired_diff_mean.diff()
        window = step.loc[1998:2025]
        sd = float(window.std())
        anchor_z = {
            year: float(step.loc[year] / sd) if sd > 0 and year in step.index else math.nan
            for year in anchors
        }
        largest_year = int(window.abs().idxmax())
        records.append(
            {
                "series": series,
                "first_year_paired_ci_excludes_zero": first,
                "n_years_significant": int(len(significant)),
                "n_years": int(len(group)),
                "sign_changes_in_paired_difference": sign_changes,
                "paired_difference_1996": float(group.paired_diff_mean.loc[1996]),
                "paired_difference_2010": float(group.paired_diff_mean.loc[2010]),
                "paired_difference_2024": float(group.paired_diff_mean.loc[2024]),
                "peak_abs_paired_difference_year": int(
                    group.paired_diff_mean.abs().idxmax()),
                "peak_abs_paired_difference": float(group.paired_diff_mean.abs().max()),
                "sd_of_year_on_year_change_1998_2025": sd,
                "anchor_2004_z": anchor_z[2004],
                "anchor_2019_z": anchor_z[2019],
                "largest_year_on_year_change_year": largest_year,
                "largest_year_on_year_change_z": float(window.abs().max() / sd) if sd > 0 else math.nan,
                "abrupt_anchor_artifact": bool(
                    max(abs(anchor_z[2004]), abs(anchor_z[2019])) >= 3.0
                ),
            }
        )
    frame = pd.DataFrame(records)
    anchor_abs = frame[["anchor_2004_z", "anchor_2019_z"]].abs()
    worst_row = frame.loc[anchor_abs.max(axis=1).idxmax()]
    largest = frame.loc[frame.largest_year_on_year_change_z.idxmax()]
    facts = {
        "max_anchor_z": float(anchor_abs.to_numpy().max()),
        "max_anchor_series": str(worst_row["series"]),
        "largest_jump_z": float(largest["largest_year_on_year_change_z"]),
        "largest_jump_year": int(largest["largest_year_on_year_change_year"]),
        "any_abrupt_anchor_artifact": bool(frame.abrupt_anchor_artifact.any()),
    }
    return frame, facts


def non_msm_effects(annual: pd.DataFrame, wide: pd.DataFrame) -> pd.DataFrame:
    series_of_interest = (
        "plhiv_non_msm_end", "on_art_non_msm_end", "new_infections_non_msm",
        "new_diagnoses_non_msm", "new_diagnoses_non_msm_endogenous",
        "new_diagnoses_non_msm_exogenous_C1", "non_msm_dx_in_aids_stage",
        "non_msm_dx_symptom_attributed", "hiv_deaths_non_msm",
        "aids_progression_undiagnosed_non_msm",
    )
    records: list[dict[str, Any]] = []
    for series in series_of_interest:
        group = annual[annual.series == series]
        significant = group[group.ci_excludes_zero == True]  # noqa: E712
        cand = _cumulative(wide, CANDIDATE, series)
        leg = _cumulative(wide, LEGACY, series)
        stats = paired((cand - leg).tolist())
        legacy_total = float(leg.mean())
        records.append(
            {
                "series": series,
                "window": "2015-2024 (sum of annual values)",
                "legacy": legacy_total,
                "late_diagnosis": float(cand.mean()),
                "paired_difference_mean": stats["mean"],
                "paired_ci95_low": stats["ci95_low"],
                "paired_ci95_high": stats["ci95_high"],
                "ci_excludes_zero": stats["ci_excludes_zero"],
                "relative_change": (stats["mean"] / legacy_total
                                    if abs(legacy_total) > 1e-12 else math.nan),
                "n_years_with_significant_annual_difference": int(len(significant)),
                "n_years": int(len(group)),
            }
        )
    return pd.DataFrame(records)


def historical_model_comparison(hist: dict[str, Any], wide: pd.DataFrame) -> pd.DataFrame:
    scalars = hist["scalars"]
    imp = hist["implausibility"]
    external = hist["external"].set_index("check_id")

    def arm_mean(arm: str, column: str) -> float:
        return float(scalars[scalars.tuple_id == arm][column].mean())

    def arm_sd(arm: str, column: str) -> float:
        return float(scalars[scalars.tuple_id == arm][column].std(ddof=1))

    rows: list[dict[str, Any]] = []

    def add(measure: str, column: str, observed: Any, lower: Any, upper: Any,
            tier: str, comparison: str, note: str) -> None:
        cand, leg = arm_mean(CANDIDATE, column), arm_mean(LEGACY, column)
        diff = paired(
            (scalars[scalars.tuple_id == CANDIDATE].set_index("seed")[column]
             - scalars[scalars.tuple_id == LEGACY].set_index("seed")[column]).tolist()
        )
        rows.append({
            "measure": measure, "observed_or_interval": observed,
            "interval_lower": lower, "interval_upper": upper,
            "evidence_tier": tier, "comparison_type": comparison,
            "legacy_mean": leg, "legacy_sd": arm_sd(LEGACY, column),
            "late_diagnosis_mean": cand, "late_diagnosis_sd": arm_sd(CANDIDATE, column),
            "paired_difference_mean": diff["mean"],
            "paired_ci95_low": diff["ci95_low"], "paired_ci95_high": diff["ci95_high"],
            "ci_excludes_zero": diff["ci_excludes_zero"], "note": note,
        })

    add("MSM diagnoses 2024", "dx2024", 318.0, "", "", "Tier 1",
        "primary history-matching target",
        "Fitting-contract performance, not out-of-sample validation")
    add("MSM new infections 2024", "msm_new_infections_2024", 310.0, 250.0, 365.0, "Tier 2",
        "Tier-2 plausibility interval",
        "SHM 2025 ECDC-tool estimate; shares surveillance inputs with the targets")
    add("End-2024 undiagnosed MSM", "msm_undiagnosed_end2024", 805.0, 690.0, 900.0, "Tier 2",
        "Tier-2 plausibility interval",
        "Back-calculated from CD4 at diagnosis; the discriminating construct the model lacks")
    add("End-2024 MSM PLHIV", "msm_plhiv_end2024", 15525.0, 15415.0, 15625.0, "Tier 2",
        "Tier-2 plausibility interval", "Both structures remain far below")
    add("End-2024 MSM on ART", "msm_on_art_end2024", 14174.0, "", 14174.0, "Tier 1",
        "one-sided safety bound", "Both within bound; late diagnosis has less headroom")
    add("MSM diagnoses in the model AIDS state, 2024", "msm_dx_in_aids_2024", 58.0, "", 58.0,
        "Tier 1", "one-sided safety bound",
        "Model clinical AIDS is a narrower construct than observed advanced presentation")
    add("MSM HIV deaths 2015-2024", "msm_hiv_deaths_2015_2024", "", "", "", "no direct target",
        "model output", "Not a fitted quantity; see mortality_decomposition.csv")
    add("All-population PLHIV 2023", "plhiv_all_2023", 25461.26, 22189.29, 28441.28, "Tier 2",
        "Tier-2 plausibility interval", "UNAIDS/Spectrum; cross-model triangulation")
    add("Diagnosed-patient HIV deaths 2010-2014", "hiv_deaths_diagnosed_all_2010_2014", 750.0,
        "", 750.0, "Tier 1", "one-sided safety bound", "Both within bound")
    add("All-population HIV deaths 2023", "hiv_deaths_all_2023", 70.89, 51.42, 92.72, "Tier 2",
        "descriptive only",
        "Both structures exceed the UNAIDS modelled AIDS-death range; late diagnosis by more")

    cand_imp = imp[imp.tuple_id == CANDIDATE]
    leg_imp = imp[imp.tuple_id == LEGACY]
    rows.append({
        "measure": "Maximum primary implausibility I_max over the frozen 13 targets",
        "observed_or_interval": "<= 3 (frozen rule)", "interval_lower": "", "interval_upper": 3.0,
        "evidence_tier": "Tier 1", "comparison_type": "frozen target contract",
        "legacy_mean": float(leg_imp.primary_implausibility.max()), "legacy_sd": math.nan,
        "late_diagnosis_mean": float(cand_imp.primary_implausibility.max()),
        "late_diagnosis_sd": math.nan, "paired_difference_mean": math.nan,
        "paired_ci95_low": math.nan, "paired_ci95_high": math.nan, "ci_excludes_zero": False,
        "note": "Compatibility with the frozen contract only; not global validity",
    })
    rows.append({
        "measure": "Mean primary implausibility over the frozen 13 targets",
        "observed_or_interval": "", "interval_lower": "", "interval_upper": "",
        "evidence_tier": "Tier 1", "comparison_type": "frozen target contract",
        "legacy_mean": float(leg_imp.primary_implausibility.mean()), "legacy_sd": math.nan,
        "late_diagnosis_mean": float(cand_imp.primary_implausibility.mean()),
        "late_diagnosis_sd": math.nan, "paired_difference_mean": math.nan,
        "paired_ci95_low": math.nan, "paired_ci95_high": math.nan, "ci_excludes_zero": False,
        "note": "Non-compensatory rule uses the maximum, not the mean",
    })
    del external
    return pd.DataFrame(rows)


def external_evidence_review(hist: dict[str, Any]) -> pd.DataFrame:
    frame = hist["external"].copy()
    independence = {
        "Tier 1": "direct surveillance; independent of the fitted quantity only where the "
                  "series does not itself enter the target contract",
        "Tier 2": "model-derived (ECDC tool / Spectrum) from surveillance inputs shared with "
                  "the calibration targets; agreement is cross-model triangulation, not "
                  "independent validation",
    }
    frame["input_independence_assessment"] = frame["epistemic_tier"].map(independence)
    frame["permitted_use"] = np.where(
        frame["epistemic_tier"] == "Tier 2", "context_or_structural_sensitivity",
        np.where(frame["comparison_type"] == "one-sided safety bound",
                 "safety_bound_only_cannot_confirm", "context_only"),
    )
    frame["late_diagnosis_direction"] = frame["candidate_moves"]
    return frame


# ---------------------------------------------------------------------------
# 3. Registers, AE fields, frozen design
# ---------------------------------------------------------------------------
SOURCES: list[dict[str, str]] = [
    # (path, artifact_type, role, status, seeds, horizon, headline_eligible, notes)
    ("TASK_BRIEF_ABSTRACT_EVIDENCE_PACKAGE_2026-09-23.md", "markdown",
     "governing specification for this package", "current", "", "",
     "not_a_result", "Task brief"),
    ("TASK_BRIEF_STAGE_DIAGNOSIS_CANDIDATE_BASELINE_REVIEW_2026-09-22.md", "markdown",
     "specification for the whole-model late-diagnosis review", "current", "", "",
     "not_a_result", "Defines the three permitted classification labels"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/STATUS.md",
     "markdown", "review status at checkpoint 1", "superseded_by_this_package", "", "",
     "not_a_result", "Phases 4-8 were NOT STARTED; completed here from saved outputs"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/DATA_INTEGRITY_NOTE.md",
     "markdown", "provenance and integrity audit, 60/60 checks", "complete",
     "202300-202329", "1975-2025", "yes",
     "Reproduced byte-identically in this session except for the gzip mtime header"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/integrity_checks.json",
     "json", "machine-readable integrity checks", "complete", "202300-202329", "1975-2025",
     "yes", "60 checks, 0 blocking failures"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/target_implausibility_table.csv",
     "csv", "13 primary targets, both structures", "complete", "202300-202329", "1996-2024",
     "yes", "I_max and per-target implausibility; frozen contract"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/external_evidence_comparison.csv",
     "csv", "external and Tier-2 comparisons", "complete", "202300-202329", "1996-2024",
     "conditional", "Tier-2 rows are cross-model triangulation, not independent validation"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/candidate_vs_legacy_annual.csv",
     "csv", "90 annual series, paired differences", "complete", "202300-202329", "1996-2025",
     "yes", "Primary input to the trajectory-shape and non-MSM review"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/candidate_vs_legacy_milestones.csv",
     "csv", "milestone-year extract of the annual table", "complete", "202300-202329",
     "1996-2025", "yes", "Derived from the annual table"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/seed_level_long_1996_2025.csv.gz",
     "csv.gz", "seed-level annual values, both structures", "complete", "202300-202329",
     "1996-2025", "yes", "162,000 rows; the paired record used for every CI here"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/seed_level_scalars.csv",
     "csv", "seed-level scalar metrics and target draws", "complete", "202300-202329",
     "1996-2024", "yes", "Used for the section 5.1 controls"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/candidate_configuration.json",
     "json", "frozen late-diagnosis configuration", "complete", "", "", "yes",
     "Rebuilt from the frozen design and hash-verified; not a production configuration"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/legacy_configuration.json",
     "json", "frozen legacy configuration", "complete", "", "", "yes",
     "Rebuilt from the frozen design and hash-verified; not a production configuration"),
    ("outputs/calibration/diagnosis_triangulation_orchestration_20260922/REPORT.md", "markdown",
     "diagnosis-mechanism triangulation", "complete", "202300-202329", "1975-2025",
     "conditional", "Classification 'Unresolvable with current disease states' (rule R5)"),
    ("outputs/calibration/diagnosis_triangulation_orchestration_20260922/AIDS_DEFINITION_ADJUDICATION.md",
     "markdown", "AIDS construct adjudication", "complete", "", "", "yes",
     "Model AIDS is clinical AIDS; mortality, utility and cost parameters are CD4-based"),
    ("outputs/calibration/diagnosis_triangulation_orchestration_20260922/stage_diagnosis_decomposition.csv",
     "csv", "exact post-2015 diagnosis accounting identity", "complete", "202300-202329",
     "2015-2024", "yes", "Identity residual 0.0"),
    ("outputs/calibration/diagnosis_triangulation_orchestration_20260922/identifiability.json",
     "json", "local sensitivity and identifiability diagnostics", "complete", "", "",
     "context_only", "Backbone 1788: routine and symptom effects not separately informed"),
    ("MODEL_LIMITATIONS.md", "markdown", "structural limitations register", "current", "", "",
     "yes", "L-10 is resolved by the A-30 decomposition and should be updated"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/matched_hta_20260922/manifest.json",
     "json", "matched substitution HTA provenance", "complete", "42-56", "2025-2064", "yes",
     "All seven output hashes verified in this session"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/matched_hta_20260922/REPORT.md",
     "markdown", "matched substitution HTA report", "complete", "42-56", "2025-2064", "yes",
     "Structural-sensitivity framing preserved"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/matched_hta_20260922/per_seed_economics.csv",
     "csv", "matched substitution seed-level economics", "complete", "42-56", "2025-2064",
     "yes", "180 rows; no person-time or AE component retained"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/matched_hta_20260922/decision_summary.csv",
     "csv", "matched substitution decision summary", "complete", "42-56", "2025-2064", "yes",
     "Column names alone do not carry the sign-instability warning; see pooled table"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/matched_hta_20260922/paired_structure_difference_summary.csv",
     "csv", "candidate-minus-legacy paired differences", "complete", "42-56", "2025-2064",
     "yes", "Paired on identical seed and scenario"),
    ("outputs/calibration/stage_diagnosis_candidate_baseline_review_20260922/matched_hta_20260922/pooled_price_thresholds.csv",
     "csv", "pooled price roots with sign-instability flag", "complete", "42-56", "2025-2064",
     "conditional", "Six of thirty roots are sign-unstable and must carry a warning"),
    ("tools/run_stage_diagnosis_matched_hta.py", "python",
     "matched substitution HTA harness", "complete", "", "", "not_a_result",
     "Extended by tools/run_matched_deployment_hta.py"),
    ("tests/test_stage_diagnosis_matched_hta.py", "python",
     "tests for the matched substitution harness", "complete", "", "", "not_a_result", ""),
    ("configs/hta_runtime_config.json", "json", "HTA runtime and scenario configuration",
     "current", "", "2025-2064", "yes",
     "Already defines all ten deployment scenarios; unchanged by this task"),
    ("configs/hta_costing_config.json", "json", "economic inputs, utilities, AE items",
     "current", "", "", "yes",
     "Two lenacapavir AE items; no oral-PrEP AE item; both PrEP utilities are 1.0"),
    ("outputs/hta_results/ae_adherence_corridor_20260914_225729/REPORT.md", "markdown",
     "AE, efficacy and persistence evidence review", "complete", "", "", "conditional",
     "Supports the sensitivity design, not a fitted AE value"),
    ("outputs/hta_results/ae_adherence_corridor_20260914_225729/SOURCE_VERIFICATION.md",
     "markdown", "verification of the three asymmetry claims", "complete", "", "", "yes",
     "Confirms 0.01115 QALY per discounted active-lenacapavir person-year"),
    ("outputs/hta_results/ae_adherence_corridor_20260914_225729/PSA_COVERAGE_GAP.md", "markdown",
     "PSA cannot express the AE/adherence corridor", "complete", "", "", "yes",
     "Corridor must be carried as deterministic scenario analysis"),
    ("outputs/hta_results/a30_dqaly_decomposition_20260914_190815/REPORT.md", "markdown",
     "exact QALY decomposition at the legacy production configuration", "complete", "42-56",
     "2025-2064", "context_only",
     "Different epidemiological configuration; do not mix with the matched structures"),
    ("outputs/hta_results/a30_dqaly_decomposition_20260914_190815/analysis_summary.json", "json",
     "A-30 control summary", "complete", "42-56", "2025-2064", "context_only",
     "576/576 controls pass, max |residual| 7.6e-7 QALYs"),
    ("outputs/hta_results/a16_stochastic_headline_20260911_030744/A16_STOCHASTIC_HEADLINE.md",
     "markdown", "stochastic estimand and price-quantity definitions", "superseded",
     "42-56", "2025-2064", "context_only",
     "Carries its own decision-aid disclaimer; parameter set is not the matched structures"),
    ("outputs/hta_results/a11_task0_thesis_config_current_code_20260910_041947",
     "directory", "older deterministic expansion outputs", "superseded", "42 (deterministic)",
     "2025-2064", "context_only",
     "n=1 deterministic, rank012 config; must not be presented as matched stochastic evidence"),
    ("outputs/hta_results/rivm_prep_targets_20260914_134653/RIVM_PrEP_target_admissibility.md",
     "markdown", "admissibility of the RIVM PrEP programme series", "complete", "", "",
     "context_only", "CSG series is a verified lower bound on uptake"),
    # --- Phase 2 products: the completed matched deployment run ---------------
    ("outputs/abstract_evidence_20260923/matched_deployment/manifest.json", "json",
     "matched deployment run provenance", "complete", "42-56", "2025-2064", "yes",
     "run_status completed_from_existing; simulation and finalization code hashed separately"),
    ("outputs/abstract_evidence_20260923/matched_deployment/per_seed_economics.csv", "csv",
     "matched deployment seed-level economics, 185 retained fields", "complete", "42-56",
     "2025-2064", "yes",
     "300 rows; 2 structures x 10 scenarios x 15 seeds; read with float_precision=round_trip"),
    ("outputs/abstract_evidence_20260923/matched_deployment/decision_summary.csv", "csv",
     "decision summary under both AE conventions", "complete", "42-56", "2025-2064", "yes",
     "Carries root_exists and sign_stability alongside the price quantity name"),
    ("outputs/abstract_evidence_20260923/matched_deployment/pooled_price_thresholds.csv", "csv",
     "pooled price roots with existence and sign-stability flags", "complete", "42-56",
     "2025-2064", "conditional",
     "18 of 96 rows need a qualified statement; 12 have no non-negative root"),
    ("outputs/abstract_evidence_20260923/matched_deployment/paired_structure_difference_summary.csv",
     "csv", "candidate-minus-legacy paired differences, all ten scenarios", "complete",
     "42-56", "2025-2064", "yes", "Paired on identical seed and scenario"),
    ("outputs/abstract_evidence_20260923/matched_deployment/gate_reproduction.csv", "csv",
     "reproduction gate against the completed substitution run", "complete", "42-56",
     "2025-2064", "yes", "540/540 pass at ULP distance 0"),
    ("outputs/abstract_evidence_20260923/matched_deployment/gate_semantics.csv", "csv",
     "deployment-family semantics gate", "complete", "42-56", "2025-2064", "yes",
     "18/18 pass"),
    ("outputs/abstract_evidence_20260923/matched_deployment/gate_identities.csv", "csv",
     "arithmetic identity gate", "complete", "42-56", "2025-2064", "yes",
     "8/8 pass, worst error 2.3e-13"),
    ("tools/run_matched_deployment_hta.py", "python",
     "matched deployment harness", "current", "", "", "not_a_result",
     "Simulation and finalization hashes are recorded separately in the run manifest"),
    ("tests/test_matched_deployment_hta.py", "python",
     "tests for the matched deployment harness", "current", "", "", "not_a_result",
     "35 tests including regression cover for both 2026-09-23 harness failures"),
]


def source_register() -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for index, (path, artifact, role, status, seeds, horizon, headline, notes) in enumerate(
        SOURCES, start=1
    ):
        full = ROOT / path
        if full.is_dir():
            digest = "directory (not hashed)"
        elif full.exists():
            digest = sha256_file(full)
        else:
            digest = "MISSING"
        records.append(
            {
                "source_id": f"SRC{index:03d}",
                "path": path,
                "artifact_type": artifact,
                "role": role,
                "status": status,
                "config_or_file_sha256": digest,
                "seeds_or_sample": seeds,
                "horizon": horizon,
                "headline_eligible": headline,
                "notes": notes,
            }
        )
    return pd.DataFrame(records)


AE_FIELDS: list[tuple[str, str, str, str]] = [
    ("int_qaly_utility_decrement_adverse_events_disc",
     "discounted QALYs", "intervention arm",
     "The booked ISR/nodule decrement. Removing it IS the symmetric no-ISR sensitivity."),
    ("ref_qaly_utility_decrement_adverse_events_disc",
     "discounted QALYs", "comparator arm",
     "Must be exactly zero under the current configuration; retained so that the zero is "
     "verified rather than assumed."),
    ("int_qaly_utility_decrement_adverse_events_undisc",
     "undiscounted QALYs", "intervention arm", "Undiscounted counterpart, for reporting."),
    ("ref_qaly_utility_decrement_adverse_events_undisc",
     "undiscounted QALYs", "comparator arm", "Undiscounted counterpart, for reporting."),
    ("int_qaly_susceptible_lenacapavir_disc",
     "discounted person-years", "intervention arm",
     "Active lenacapavir person-time at utility 1.0. Multiplying by d_lena must reproduce "
     "the booked decrement exactly."),
    ("ref_qaly_susceptible_lenacapavir_disc",
     "discounted person-years", "comparator arm",
     "Zero in every scenario under the current arm definitions; retained as a check."),
    ("int_qaly_susceptible_lenacapavir_waning_disc",
     "discounted person-years", "intervention arm",
     "Waning lenacapavir person-time. adverse_events.py does NOT apply the decrement to it. "
     "Retained so the exclusion is visible and can be tested as a separate sensitivity."),
    ("ref_qaly_susceptible_lenacapavir_waning_disc",
     "discounted person-years", "comparator arm", "Zero under the current arm definitions."),
    ("int_qaly_susceptible_oral_prep_disc",
     "discounted person-years", "intervention arm",
     "Oral-PrEP person-time. Enters the reweighting only through d_oral, which is zero by "
     "configuration; retained so any future oral AE weight can be applied exactly."),
    ("ref_qaly_susceptible_oral_prep_disc",
     "discounted person-years", "comparator arm",
     "Oral-PrEP person-time given up under substitution. Required for d_oral > 0 variants."),
    ("isr_qaly_decrement_per_lena_person_year",
     "QALYs per person-year", "configuration",
     "d_lena, the prevalence-weighted sum over lenacapavir AE items (0.01115 as configured)."),
    ("delta_qaly", "discounted QALYs", "intervention minus comparator",
     "The configured-AE-convention result."),
    ("delta_ae_qaly_disc", "discounted QALYs", "intervention minus comparator",
     "DeltaAE. Equals the intervention decrement minus the comparator decrement."),
    ("delta_qaly_excluding_ae", "discounted QALYs", "intervention minus comparator",
     "DeltaQALY - DeltaAE, the quantity the brief calls DeltaQALY_excluding_AE."),
    ("delta_qaly_no_isr", "discounted QALYs", "intervention minus comparator",
     "The symmetric no-ISR result. Equals delta_qaly_excluding_ae because d_oral = 0."),
    ("ae_identity_max_abs_error_disc", "discounted QALYs", "both arms",
     "Worst absolute residual of |booked decrement + d_lena x lena person-years|. The "
     "reconstruction may be called exact only while this is at floating-point scale."),
    ("delta_cost", "discounted EUR", "intervention minus comparator",
     "Unchanged by the AE convention: both AE items carry cost zero."),
    ("int_cost_adverse_events_disc", "discounted EUR", "intervention arm",
     "Retained to verify that the AE cost channel is zero, so no-ISR moves QALYs only."),
    ("lena_product_cost", "discounted EUR", "intervention arm",
     "The price-sensitive lenacapavir bundle; the denominator of every price root."),
    ("total_prep_person_years_disc_reference", "discounted person-years", "comparator arm",
     "Oral + lenacapavir + waning. Used by the deployment-family semantics gate."),
    ("total_prep_person_years_disc_intervention", "discounted person-years", "intervention arm",
     "Used by the deployment-family semantics gate."),
]


def ae_reweighting_fields() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "field": field,
                "unit": unit,
                "arm": arm,
                "required_for": "symmetric no-ISR reweighting",
                "why": why,
            }
            for field, unit, arm, why in AE_FIELDS
        ]
    )


def frozen_design(matched: dict[str, Any]) -> dict[str, Any]:
    from tools.run_matched_deployment_hta import (  # noqa: E402
        DEPLOYMENT_FAMILY, DEPLOYMENT_LEVEL, DEPLOYMENT_SCENARIOS, EXPECTED_ARMS,
        build_plan, isr_decrement_per_person_year,
    )

    costing = json.loads((ROOT / "configs/hta_costing_config.json").read_text())
    seeds = list(range(42, 57))
    plan = build_plan(seeds)
    return {
        "design_id": "matched_deployment_20260923",
        "status": "COMPLETE — executed 2026-09-23 and finalized from saved outputs.",
        "brief": "TASK_BRIEF_ABSTRACT_EVIDENCE_PACKAGE_2026-09-23.md",
        "created": date.today().isoformat(),
        "runner": "tools/run_matched_deployment_hta.py",
        "runner_sha256": sha256_file(ROOT / "tools/run_matched_deployment_hta.py"),
        "tests": "tests/test_matched_deployment_hta.py",
        "tests_sha256": sha256_file(ROOT / "tests/test_matched_deployment_hta.py"),
        "epidemic_structures": {
            "legacy": {
                "tuple_id": LEGACY,
                "config": "outputs/calibration/stage_diagnosis_candidate_baseline_review_"
                          "20260922/legacy_configuration.json",
                "config_sha256": matched["manifest"]["structures"]["legacy"][
                    "frozen_config_sha256"],
                "role": "primary interim baseline",
            },
            "candidate": {
                "tuple_id": CANDIDATE,
                "config": "outputs/calibration/stage_diagnosis_candidate_baseline_review_"
                          "20260922/candidate_configuration.json",
                "config_sha256": matched["manifest"]["structures"]["candidate"][
                    "frozen_config_sha256"],
                "role": "late-diagnosis structural sensitivity",
            },
        },
        "hta_runtime_config": "configs/hta_runtime_config.json",
        "hta_runtime_config_sha256": sha256_file(ROOT / "configs/hta_runtime_config.json"),
        "hta_costing_config": "configs/hta_costing_config.json",
        "hta_costing_config_sha256": sha256_file(ROOT / "configs/hta_costing_config.json"),
        "scenario_matrix": [
            {
                "scenario": name,
                "deployment_family": DEPLOYMENT_FAMILY[name],
                "deployment_level": DEPLOYMENT_LEVEL.get(name),
                "comparator_arm": EXPECTED_ARMS[name][0],
                "intervention_arm": EXPECTED_ARMS[name][1],
                "decision_role": (
                    "attribution context only" if name == "base_case_context"
                    else "contextual benchmark, not a forecast"
                    if name == "scenario_c_oral_expansion_benchmark"
                    else "decision scenario against oral-PrEP SOC"
                ),
                "already_in_completed_run": True,
            }
            for name in DEPLOYMENT_SCENARIOS
        ],
        "stochastic_seeds": seeds,
        "seed_separation": (
            "These are HTA seeds. They are disjoint in role from the 30 history-matching "
            "confirmation seeds 202300-202329 and must never be pooled into one uncertainty "
            "sample."
        ),
        "ae_convention": {
            "configured": "lenacapavir ISR and persistent nodule decrements as configured; "
                          "zero oral-PrEP AE decrement",
            "isr_qaly_decrement_per_lena_person_year": isr_decrement_per_person_year(costing),
            "items": [
                {
                    "id": item["id"],
                    "prevalence": item["prevalence"]["value"],
                    "utility_decrement": item["utility_decrement"]["value"],
                    "cost": item["cost"]["value"],
                }
                for item in costing["adverse_events"]["items"]
            ],
            "sensitivity": "symmetric no-ISR: set the lenacapavir ISR/nodule decrement to "
                           "zero, derived exactly from retained fields; no rerun required",
        },
        "wtp_tiers_eur_per_qaly": list(ZIN_WTP_TIERS),
        "base_annual_lena_price_eur": 41110.44,
        "horizon": {"freeze_year": 2025, "evaluation_years": 40, "end_year": 2064},
        "perspective": "societal (primary)",
        "discounting": {"costs": 0.04, "health_effects": 0.015},
        "trajectory_plan": plan,
        "gates": [
            "reproduction: the six completed scenarios must reproduce the completed matched "
            "substitution run to 1e-9 on delta_cost, delta_qaly and infections_averted",
            "semantics: add-on and oral expansion must increase total discounted PrEP "
            "person-time; substitution must preserve it within 2%",
            "identities: NMB, no-ISR NMB and the AE reconstruction must close to "
            "floating-point scale",
        ],
        "unchanged": [
            "targets", "uncertainty terms", "implausibility cutoff", "WTP thresholds",
            "lenacapavir price", "parameter bounds", "every non-diagnosis configuration leaf",
        ],
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------
def render_review(facts: dict[str, Any]) -> str:
    f = facts
    def n(value: float, digits: int = 2) -> str:
        return f"{value:,.{digits}f}"

    return f"""# Whole-model review of the late-diagnosis structure `D_1788_v109`

Completes phases 4, 5 and 6 of `TASK_BRIEF_STAGE_DIAGNOSIS_CANDIDATE_BASELINE_REVIEW_2026-09-22.md`
from saved confirmation outputs. No historical simulation was run. Comparator is the matched
legacy structure `D_1788_v000` on the identical 30 confirmation seeds (202300-202329), identical
backbone 1788, and identical non-diagnosis configuration.

All intervals are two-sided 95% Student-t intervals on the paired seed differences, n = 30.

## Phase 4 — mortality and AIDS burden

### Where the extra deaths are

Cumulative HIV deaths 2015-2024 rise by **{n(f['deaths_msm_total'])}** in MSM
(legacy {n(f['deaths_msm_legacy'])} to late diagnosis {n(f['deaths_msm_candidate'])}) and by
**{n(f['deaths_non_msm_total'])}** in non-MSM, whose interval
[{n(f['deaths_non_msm_low'])}, {n(f['deaths_non_msm_high'])}] contains zero. The change is an
MSM change.

Within MSM the extra deaths split as:

| diagnosis / ART state | legacy | late diagnosis | change | share of the MSM change |
|---|---:|---:|---:|---:|
| undiagnosed, off ART | {n(f['deaths_undx_legacy'])} | {n(f['deaths_undx_candidate'])} | {n(f['deaths_undx_diff'])} | {f['deaths_undx_share']:.1%} |
| diagnosed, off ART | {n(f['deaths_dxoff_legacy'])} | {n(f['deaths_dxoff_candidate'])} | {n(f['deaths_dxoff_diff'])} | {f['deaths_dxoff_share']:.1%} |
| diagnosed, on ART | {n(f['deaths_dxon_legacy'])} | {n(f['deaths_dxon_candidate'])} | {n(f['deaths_dxon_diff'])} | {f['deaths_dxon_share']:.1%} |

No deaths occur in the undiagnosed-on-ART state in either structure, as the integrity audit
already established.

### Does the death *rate* also worsen? Yes, and it is mostly composition

MSM deaths per 1,000 PLHIV person-years rise from **{n(f['rate_legacy'], 3)}** to
**{n(f['rate_candidate'], 3)}**, a change of **{n(f['rate_total_change'], 3)}**. A shift-share
split closes exactly (largest residual {f['shift_share_closure_max_abs']:.1e} per 1,000):

- **{f['rate_composition_share']:.0%}** is a change in the *mix* of person-time. The share of
  MSM PLHIV person-time spent undiagnosed and off ART rises from
  {f['share_undx_legacy']:.4f} to {f['share_undx_candidate']:.4f}, and that stratum carries a
  death rate several times the on-ART rate.
- **{f['rate_within_share']:.0%}** is a change in *within-stratum* rates. The dominant term is
  the on-ART death rate, which rises from {n(f['rate_onart_legacy'], 3)} to
  {n(f['rate_onart_candidate'], 3)} per 1,000 on-ART person-years.

The on-ART rate is not a free parameter, so its rise is a composition effect one level down: the
AIDS-stage share of MSM PLHIV person-time rises from {f['aids_share_legacy']:.4f} to
{f['aids_share_candidate']:.4f}, and end-2024 MSM AIDS-stage stock rises by
{n(f['aids_stock_diff'])}.

Note the counter-direction: deaths per 1,000 *undiagnosed* person-years **fall** by
{n(abs(f['rate_undx_diff']), 3)}. The reservoir grows faster than the deaths inside it, because
the extra reservoir is disproportionately chronic-stage rather than AIDS-stage.

### Which of the brief's four explanations applies

1. **More people living with HIV** — yes, and it is the largest single channel. End-2024 MSM
   PLHIV rises by {n(f['plhiv_msm_diff'])}.
2. **More people untreated or diagnosed later** — yes, and it is the dominant driver of the
   *rate* change, at {f['rate_composition_share']:.0%} of it.
3. **The model's progression and mortality mapping** — yes, partly, and this is the
   uncomfortable part. `AIDS_DEFINITION_ADJUDICATION.md` establishes that the AIDS compartment
   is *clinical* AIDS (CASCADE, an AIDS-defining illness) while `disease.mortality.aids` is a
   CD4 <200 Glaubius rate, and the off-ART utility and the Popping "very late" cost tier are
   also CD4-based. The late-diagnosis structure pushes person-time into exactly that
   compartment, so it loads the seam. The direction of the resulting bias is not identified.
4. **Effects that cannot be separated without a new counterfactual** — the split between
   "diagnosed later" and "larger hidden epidemic" cannot be made from these runs. The
   orchestration's rule-R5 classification says the same thing for the same reason.

### The AIDS burden

The exact post-2015 diagnosis identity (`stage_diagnosis_decomposition.csv`, residual 0.0) gives
the extra {n(f['dx_identity_total'])} MSM diagnoses in 2015-2024 as

    {n(f['dx_identity_total'])} = {n(f['dx_reservoir_2015'])} carried reservoir
        + {n(f['dx_extra_infections'])} additional infections
        {n(f['dx_reservoir_left'])} change in the reservoir left at 2025
        {n(f['dx_other_exits'])} other exits

Of those extra diagnoses, {n(f['dx_stage_chronic'])} are made in the chronic stage and
{n(f['dx_stage_aids'])} in the AIDS stage; {n(f['dx_symptom_attributed'])} are attributed to the
symptom hazard. **The mechanism's own symptom channel is roughly
{f['symptom_share_of_dx_change']:.0%} of the diagnosis change.** Most of the improvement in the
2024 diagnosis target comes from a larger epidemic, not from re-timed diagnosis. That is the
single most important qualification on the candidate.

## Phase 5 — trajectory shape

**When do the trajectories diverge, and is it abrupt?** Divergence is gradual and begins early.
The paired difference in the MSM undiagnosed reservoir is significant from 1996, but with the
*opposite* sign: the candidate's 1996 anchor (0.820) exceeds the legacy's (0.739), so the
candidate tests MSM slightly more in 1996-1999. The difference crosses zero around 2003-2004,
grows to a peak of {n(f['reservoir_peak'])} in {int(f['reservoir_peak_year'])}, then narrows to
{n(f['reservoir_2024_diff'])} by end-2024. Calling this structure uniformly "later diagnosis" is
wrong before about 2000.

**Is there an artifact at the 2004 or 2019 anchors?** No. Across the eleven series in
`trajectory_divergence.csv`, the largest year-on-year jump in a paired difference at either
anchor year is {n(f['max_anchor_z'], 2)} standard deviations of that series' own year-on-year
changes ({f['max_anchor_series']} at 2019), below the 3-SD screen, and no series is flagged. The
single largest jump anywhere, {n(f['largest_jump_z'], 2)} SD, falls in
{int(f['largest_jump_year'])}, away from any anchor. The anchors are interpolated, not stepped.

**Why is 2010 incidence below the external interval, 2020 above it, and 2024 inside it?** The
Tier-2 ECDC-tool series is not a smooth target and neither structure tracks its shape. In 2010
both structures are below ({n(f['inf2010_candidate'])} candidate, {n(f['inf2010_legacy'])}
legacy, against 680-745); in 2020 **both are far above** ({n(f['inf2020_candidate'])} and
{n(f['inf2020_legacy'])} against an interval of 160-190 around a point estimate of 175), the
candidate by {f['inf2020_gap_pct']:.0%} of that point estimate; only in 2024 is the candidate
inside. The 2024 agreement is therefore a single-year coincidence on a
series the model misses badly one decade earlier and five years earlier. This is an inconvenient
result and it is reported rather than dropped.

**Does ART catch up?** Linkage is not degraded: ART coverage among diagnosed MSM is only
{n(abs(f['art_coverage_diff']) * 100, 2)} percentage points lower at end-2024. But the extra PLHIV are not
all diagnosed, so ART stock rises by only {n(f['on_art_msm_diff'])} against
{n(f['plhiv_msm_diff'])} extra PLHIV; the balance is the {n(f['reservoir_2024_diff'])} larger
undiagnosed reservoir.

**Are non-MSM outputs affected?** Not materially. The symptom hazard does act on undiagnosed
AIDS-stage people in every population, so non-MSM is not mechanically untouched: symptom-attributed
non-MSM diagnoses rise by {n(f['non_msm_symptom_dx'], 2)} over 2015-2024, and that difference is
statistically detectable. Every non-MSM stock and outcome is otherwise indistinguishable —
non-MSM PLHIV at end-2024 changes by {n(f['non_msm_plhiv_diff'], 2)}
[{n(f['non_msm_plhiv_low'], 2)}, {n(f['non_msm_plhiv_high'], 2)}]. Total-population changes are
driven almost entirely by MSM.

**Does the candidate improve the total PLHIV and ART evidence, or only the MSM diagnosis
target?** It moves toward the external PLHIV and ART estimates without reaching them. End-2024
MSM PLHIV is {n(f['msm_plhiv_candidate'])} against a Tier-2 interval of 15,415-15,625 — still
{f['msm_plhiv_gap_pct']:.0%} below, where legacy is {f['msm_plhiv_gap_pct_legacy']:.0%} below.
The remaining gap is large and must not be described as agreement.

## Phase 6 — classification

**Label: retain as a structural sensitivity only.**

The strongest argument *for* promotion:

- data integrity passes, 60 of 60 checks;
- all 13 primary targets pass the frozen `I <= 3` rule, with the 2024 MSM diagnosis
  implausibility falling from {n(f['dx2024_imp_legacy'], 3)} to {n(f['dx2024_imp_candidate'], 3)};
- the end-2024 reservoir overshoot is small when read continuously — {n(f['reservoir_overshoot'])}
  above an upper bound of 900, {f['reservoir_overshoot_pct']:.2f}%, with 15 of 30 seeds inside;
- no Tier-1 one-sided safety bound is violated;
- 2024 MSM incidence lands inside its Tier-2 interval;
- trajectories are smooth, with no anchor artifact, and non-MSM is unaffected.

The arguments *against*, which decide it:

1. **The diagnosis improvement is mostly epidemic size, not diagnosis timing.** Only
   {f['symptom_share_of_dx_change']:.0%} of the extra diagnoses are attributable to the symptom
   hazard. A structure that fits a diagnosis target by enlarging the epidemic is not obviously a
   better description of diagnosis.
2. **Mean implausibility does not improve** ({n(f['mean_imp_legacy'], 3)} legacy to
   {n(f['mean_imp_candidate'], 3)} late diagnosis) and `I_max` worsens from
   {n(f['imax_legacy'], 3)} to {n(f['imax_candidate'], 3)}. Legacy is better on 7 of the 13
   targets, the candidate on 6. Passing the contract is not the same as fitting better.
3. **The mortality increase loads a known structural seam.** A quarter of the death-rate
   increase runs through within-stratum rates that rise because more person-time sits in a
   clinical-AIDS compartment carrying CD4 <200 mortality. The brief's phase-4 instruction was to
   identify the interpretation seam rather than fit around it; this is that seam.
4. **The tested point is a ridge point, not an estimated one.** Two predeclared clauses fail
   for this tuple in `decision_clauses.csv`: `c7_non_isolated_region` and
   `c9_not_ridge_identified`. At the candidate's *own* coordinates the local sensitivity is full rank and the symptom column is only weakly
   explained by the routine columns (R^2 = {f['ident_candidate_symptom_r2']:.2f}), so the symptom
   effect is locally distinguishable there; c9 fails because the condition number is
   {n(f['ident_candidate_condition_number'], 2)}, above the predeclared cutoff of 30. It is the
   *backbone-level* diagnostic, evaluated at the backbone centre, that returns
   R^2 = {f['ident_backbone_symptom_r2']:.2f} with condition
   {n(f['ident_backbone_condition_number'], 2)} and the label "not separately informed" for
   backbone 1788 as a whole. Combined with a narrow or isolated discovery neighbourhood, the
   structure is a point on a ridge rather than an identified estimate.
5. **External agreement is worse, not better, where it is checkable.** Model HIV deaths in 2023
   move further above the UNAIDS modelled AIDS-death range, and 2020 MSM incidence moves further
   above its Tier-2 interval. The predeclared latent-compatibility clause `c5_latent_compatible`
   fails for this tuple, because the end-2024 reservoir sits above its interval even though 2024
   incidence sits inside.

None of this is an integrity failure, a Tier-1 one-sided safety-bound violation, pathological
dynamics or an irreconcilable downstream consequence, so "reject in the tested form" is not
warranted. The three clause failures (`c5`, `c7`, `c9`) are diagnostic, not disqualifying: the
orchestration's own label for this tuple is "Unresolvable with current disease states" under
rule R5, which is a statement about what the model can distinguish, not a rejection.
Equally, "ready for matched preliminary forecasts" as the *primary* baseline would require the
mortality increase to have a defensible explanation, and the explanation available is partly a
structural-mapping artefact that the model cannot resolve.

This is exactly the brief's second label: *diagnosis fit improves, but mortality and the
AIDS/CD4 mapping prevent a defensible baseline interpretation.*

**Consequence for the abstract package.** Legacy `D_1788_v000` remains the primary interim
baseline. `D_1788_v109` is carried as a structural epidemic sensitivity, and every economic
result is reported under both structures. The phase-1 gate of the abstract brief is therefore
passed: the structure is retained as a structural sensitivity, not rejected.

**Smallest next experiment, if the user wants one.** The paired 2x2 factorial already specified
in the stage-diagnosis brief (legacy anchors x candidate anchors, symptom probability 0 x
{f['symptom_probability']:.6f}), 30 fresh common seeds per arm, 120 trajectories. It would
separate the routine-anchor contribution from the symptom contribution on the model's own
dynamic scale. It is **not** required for the interim abstract and is not proposed for approval
here.
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    hist = load_historical()
    matched = load_matched()
    wide = hist["wide"]
    annual = hist["annual"]

    controls = pd.DataFrame(
        reproduce_historical_controls(hist) + reproduce_matched_controls(matched)
    )
    controls.to_csv(out / "controls_reproduction.csv", index=False)

    mortality = mortality_decomposition(wide)
    mortality.to_csv(out / "mortality_decomposition.csv", index=False)
    shift, shift_facts = mortality_shift_share(wide)
    shift.to_csv(out / "mortality_rate_shift_share.csv", index=False)
    aids = aids_burden_decomposition(wide)
    aids.to_csv(out / "aids_burden_decomposition.csv", index=False)
    rates = normalized_rates(wide)
    rates.to_csv(out / "normalized_rates.csv", index=False)
    divergence, divergence_facts = trajectory_divergence(annual)
    divergence.to_csv(out / "trajectory_divergence.csv", index=False)
    non_msm = non_msm_effects(annual, wide)
    non_msm.to_csv(out / "non_msm_effect_summary.csv", index=False)
    comparison = historical_model_comparison(hist, wide)
    comparison.to_csv(out / "historical_model_comparison.csv", index=False)
    external = external_evidence_review(hist)
    external.to_csv(out / "external_evidence_review.csv", index=False)
    source_register().to_csv(out / "SOURCE_REGISTER.csv", index=False)
    ae_reweighting_fields().to_csv(out / "ae_reweighting_fields.csv", index=False)

    design = frozen_design(matched)
    (out / "frozen_design.json").write_text(
        json.dumps(design, indent=2) + "\n", encoding="utf-8"
    )

    # ---- facts for the narrative -----------------------------------------
    m = mortality.set_index(["window", "population", "diagnosis_art_state"])
    ext = hist["external"].set_index("check_id")
    imp = hist["implausibility"]
    cand_imp = imp[imp.tuple_id == CANDIDATE].set_index("target")
    leg_imp = imp[imp.tuple_id == LEGACY].set_index("target")
    decomp = hist["decomposition"]
    decomp = decomp[decomp.tuple_id == CANDIDATE].set_index("component")
    ann = annual.pivot_table(index="year", columns="series",
                             values=["candidate_mean", "legacy_mean", "paired_diff_mean",
                                     "paired_ci95_low", "paired_ci95_high"])
    reservoir = annual[annual.series == "msm_undiagnosed_end"].set_index("year")
    scalars = hist["scalars"]
    candidate_config = json.loads((REVIEW / "candidate_configuration.json").read_text())
    clause_table = pd.read_csv(ORCH / "decision_clauses.csv")
    clauses = clause_table[clause_table.tuple_id == CANDIDATE].iloc[0]

    def mrow(state: str, column: str) -> float:
        return float(m.loc[("2015-2024", "msm", state), column])

    dx_total = float(decomp.loc["total_change_msm_diagnoses_2015_2024", "paired_mean_change"])
    symptom = float(decomp.loc["symptom_attributed_aids_stage_dx", "paired_mean_change"])
    non_msm_indexed = non_msm.set_index("series")

    facts: dict[str, Any] = {
        "deaths_msm_total": mrow("all_states", "paired_difference_mean"),
        "deaths_msm_legacy": mrow("all_states", "legacy_cumulative_deaths"),
        "deaths_msm_candidate": mrow("all_states", "late_diagnosis_cumulative_deaths"),
        "deaths_non_msm_total": float(
            m.loc[("2015-2024", "non_msm", "all_states"), "paired_difference_mean"]),
        "deaths_non_msm_low": float(
            m.loc[("2015-2024", "non_msm", "all_states"), "paired_ci95_low"]),
        "deaths_non_msm_high": float(
            m.loc[("2015-2024", "non_msm", "all_states"), "paired_ci95_high"]),
        "deaths_undx_legacy": mrow("undiagnosed_off_art", "legacy_cumulative_deaths"),
        "deaths_undx_candidate": mrow("undiagnosed_off_art", "late_diagnosis_cumulative_deaths"),
        "deaths_undx_diff": mrow("undiagnosed_off_art", "paired_difference_mean"),
        "deaths_undx_share": mrow("undiagnosed_off_art", "share_of_population_total_change"),
        "deaths_dxoff_legacy": mrow("diagnosed_off_art", "legacy_cumulative_deaths"),
        "deaths_dxoff_candidate": mrow("diagnosed_off_art", "late_diagnosis_cumulative_deaths"),
        "deaths_dxoff_diff": mrow("diagnosed_off_art", "paired_difference_mean"),
        "deaths_dxoff_share": mrow("diagnosed_off_art", "share_of_population_total_change"),
        "deaths_dxon_legacy": mrow("diagnosed_on_art", "legacy_cumulative_deaths"),
        "deaths_dxon_candidate": mrow("diagnosed_on_art", "late_diagnosis_cumulative_deaths"),
        "deaths_dxon_diff": mrow("diagnosed_on_art", "paired_difference_mean"),
        "deaths_dxon_share": mrow("diagnosed_on_art", "share_of_population_total_change"),
        "rate_legacy": shift_facts["legacy_rate"],
        "rate_candidate": shift_facts["candidate_rate"],
        **shift_facts,
    }
    shift_indexed = shift.set_index("component")
    facts.update({
        "share_undx_legacy": float(
            shift_indexed.loc["share of PLHIV person-time: undiagnosed_off_art", "legacy_value"]),
        "share_undx_candidate": float(
            shift_indexed.loc["share of PLHIV person-time: undiagnosed_off_art",
                              "late_diagnosis_value"]),
        "rate_onart_legacy": float(
            shift_indexed.loc["deaths per 1,000 person-years: on_art", "legacy_value"]),
        "rate_onart_candidate": float(
            shift_indexed.loc["deaths per 1,000 person-years: on_art", "late_diagnosis_value"]),
        "rate_undx_diff": float(
            shift_indexed.loc["deaths per 1,000 person-years: undiagnosed_off_art", "mean"]),
        "aids_share_legacy": float(
            shift_indexed.loc["AIDS-stage share of MSM PLHIV person-time", "legacy_value"]),
        "aids_share_candidate": float(
            shift_indexed.loc["AIDS-stage share of MSM PLHIV person-time",
                              "late_diagnosis_value"]),
    })
    aids_indexed = aids.set_index("measure")
    facts.update({
        "aids_stock_diff": float(aids_indexed.loc["msm_aids_stage_end", "mean"]),
        "dx_identity_total": dx_total,
        "dx_reservoir_2015": float(
            decomp.loc["a_entering_undiagnosed_stock_2015", "paired_mean_change"]),
        "dx_extra_infections": float(
            decomp.loc["b_additional_infections_2015_2024", "paired_mean_change"]),
        "dx_reservoir_left": float(
            decomp.loc["c_reservoir_left_at_2025", "paired_mean_change"]),
        "dx_other_exits": float(decomp.loc["d_other_exits_2015_2024", "paired_mean_change"]),
        "dx_stage_chronic": float(decomp.loc["stage_split_chronic", "paired_mean_change"]),
        "dx_stage_aids": float(decomp.loc["stage_split_aids", "paired_mean_change"]),
        "dx_symptom_attributed": symptom,
        "symptom_share_of_dx_change": symptom / dx_total,
        "plhiv_msm_diff": float(ann[("paired_diff_mean", "plhiv_msm_end")].loc[2024]),
        "on_art_msm_diff": float(ann[("paired_diff_mean", "msm_on_art_end")].loc[2024]),
        "art_coverage_diff": float(
            ann[("paired_diff_mean", "msm_art_coverage_of_diagnosed_end")].loc[2024]),
        "reservoir_peak": float(reservoir.paired_diff_mean.max()),
        "reservoir_peak_year": float(reservoir.paired_diff_mean.idxmax()),
        "reservoir_2024_diff": float(reservoir.paired_diff_mean.loc[2024]),
        "max_anchor_z": divergence_facts["max_anchor_z"],
        "max_anchor_series": divergence_facts["max_anchor_series"],
        "largest_jump_z": divergence_facts["largest_jump_z"],
        "largest_jump_year": divergence_facts["largest_jump_year"],
        "inf2010_candidate": float(ext.loc["E3", "candidate_mean"]),
        "inf2010_legacy": float(ext.loc["E3", "legacy_mean"]),
        "inf2020_candidate": float(ext.loc["E2", "candidate_mean"]),
        "inf2020_legacy": float(ext.loc["E2", "legacy_mean"]),
        "inf2020_gap_pct": float(ext.loc["E2", "candidate_pct_gap_vs_observed"]),
        "non_msm_symptom_dx": float(
            non_msm_indexed.loc["non_msm_dx_symptom_attributed", "paired_difference_mean"]),
        "non_msm_plhiv_diff": float(
            ann[("paired_diff_mean", "plhiv_non_msm_end")].loc[2024]),
        "non_msm_plhiv_low": float(ann[("paired_ci95_low", "plhiv_non_msm_end")].loc[2024]),
        "non_msm_plhiv_high": float(ann[("paired_ci95_high", "plhiv_non_msm_end")].loc[2024]),
        "msm_plhiv_candidate": float(
            scalars[scalars.tuple_id == CANDIDATE]["msm_plhiv_end2024"].mean()),
        "msm_plhiv_gap_pct": abs(float(ext.loc["E6", "candidate_pct_gap_vs_observed"])),
        "msm_plhiv_gap_pct_legacy": abs(float(ext.loc["E6", "legacy_pct_gap_vs_observed"])),
        "dx2024_imp_legacy": float(
            leg_imp.loc["shm_new_diagnoses_msm@2024", "primary_implausibility"]),
        "dx2024_imp_candidate": float(
            cand_imp.loc["shm_new_diagnoses_msm@2024", "primary_implausibility"]),
        "reservoir_overshoot": float(
            scalars[scalars.tuple_id == CANDIDATE]["msm_undiagnosed_end2024"].mean() - 900.0),
        "reservoir_overshoot_pct": float(
            (scalars[scalars.tuple_id == CANDIDATE]["msm_undiagnosed_end2024"].mean() - 900.0)
            / 900.0 * 100.0),
        "mean_imp_legacy": float(leg_imp.primary_implausibility.mean()),
        "mean_imp_candidate": float(cand_imp.primary_implausibility.mean()),
        "imax_legacy": float(leg_imp.primary_implausibility.max()),
        "imax_candidate": float(cand_imp.primary_implausibility.max()),
        **{f"ident_{k}": v for k, v in
           identifiability_facts(hist["identifiability"]).items()},
        "clause_c5_latent_compatible": bool(clauses["c5_latent_compatible"]),
        "clause_c7_non_isolated_region": bool(clauses["c7_non_isolated_region"]),
        "clause_c9_not_ridge_identified": bool(clauses["c9_not_ridge_identified"]),
        "symptom_probability": float(
            candidate_config["config"]["transmission_model"]["disease"]["diagnosis"]
            ["stage_structured_diagnosis"]["aids_symptom_annual_probability"]["value"]),
    })

    (out / "LATE_DIAGNOSIS_WHOLE_MODEL_REVIEW.md").write_text(
        render_review(facts), encoding="utf-8"
    )
    (out / "computed_facts.json").write_text(
        json.dumps(facts, indent=2, default=float) + "\n", encoding="utf-8"
    )

    failures = controls[controls.status == "FAIL"]
    print(f"controls: {len(controls) - len(failures)}/{len(controls)} pass")
    if len(failures):
        print("FAILING CONTROLS:")
        print(failures.to_string(index=False))
    print(f"tables and review written to {out}")
    return 1 if len(failures) else 0


def identifiability_facts(payload: Any) -> dict[str, float]:
    """Read the local-sensitivity diagnostics for the candidate and its backbone.

    ``identifiability.json`` reports per-tuple diagnostics under ``tuples`` and a
    backbone-level diagnostic evaluated at the backbone centre under ``backbones``.
    These are different quantities and are kept apart: the candidate's own
    neighbourhood can distinguish the symptom coordinate even where the backbone
    centre's cannot.
    """
    tuples = payload.get("tuples", {})
    backbones = payload.get("backbones", {})
    candidate = tuples.get(CANDIDATE, {})
    backbone = backbones.get("1788", {}).get("raw", {})
    return {
        "candidate_symptom_r2": float(
            candidate.get("symptom_column_explained_by_routine_r2", math.nan)),
        "candidate_condition_number": float(candidate.get("condition_number", math.nan)),
        "candidate_numerical_rank": float(candidate.get("numerical_rank", math.nan)),
        "backbone_symptom_r2": float(
            backbone.get("symptom_column_explained_by_routine_r2", math.nan)),
        "backbone_condition_number": float(backbone.get("condition_number", math.nan)),
    }


if __name__ == "__main__":
    raise SystemExit(main())
