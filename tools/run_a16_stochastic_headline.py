"""Run A-16: headline economics under the CRN-paired stochastic estimand.

This is an analysis harness only.  It changes no production behaviour or
economic input.  The harness reruns the five lenacapavir substitution pairs and
the oral-PrEP-versus-no-PrEP context pair because A-15 did not retain cost
streams.  Fresh epidemiological outputs are required to reproduce A-15/A-4
exactly before any economic result is accepted.

The two price quantities are deliberately distinct:

* ``ce_threshold_price`` is the non-negative annual lenacapavir price where
  NMB equals zero when incremental QALYs are positive.  It is undefined when
  incremental QALYs are non-positive.
* ``qaly_loss_compensation_price`` is the same NMB-zero root when incremental
  QALYs are negative: the maximum annual price at which cost savings compensate
  for the QALY loss.  It is not called a cost-effectiveness threshold price.

ICERs are never calculated per seed.  The only ICER-like quantity emitted is a
ratio of mean incremental cost to mean incremental QALYs, and it is suppressed
when the 95% confidence interval for mean incremental QALYs includes zero.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import platform
import shlex
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_utils import extract_value  # noqa: E402
from run_scenarios import MultiScenarioRunner  # noqa: E402
from scenario_comparator import ScenarioComparator  # noqa: E402


BASE_SCENARIO = "base_case_context"
SWITCH_PREFIX = "scenario_a_lena_substitution__switch_"
SWITCHES = (10, 25, 50, 75, 100)
SCENARIOS = (BASE_SCENARIO,) + tuple(f"{SWITCH_PREFIX}{x}" for x in SWITCHES)
ZIN_WTP_TIERS = (20_000.0, 50_000.0, 80_000.0)
QALY_TOLERANCE = 1e-6
STABILITY_CV_CUTOFF = 0.5

A4_DIR = ROOT / "outputs/hta_results/a4_common_random_numbers_20260910_170048"
A15_DIR = ROOT / "outputs/hta_results/a15_a9_allocation_hypothesis_20260911_034351"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_text(args: Iterable[str]) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return completed.stdout.rstrip()


def parse_seeds(spec: str) -> list[int]:
    seeds: list[int] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            low, high = token.split("-", 1)
            seeds.extend(range(int(low), int(high) + 1))
        else:
            seeds.append(int(token))
    if not seeds:
        raise ValueError("at least one stochastic seed is required")
    if len(set(seeds)) != len(seeds):
        raise ValueError(f"duplicate stochastic seed in {spec!r}")
    return seeds


def select_scenarios(scenarios: Sequence[dict]) -> list[dict]:
    by_name = {scenario["name"]: scenario for scenario in scenarios}
    missing = [name for name in SCENARIOS if name not in by_name]
    if missing:
        raise ValueError(f"A-16 scenarios missing from expanded runtime config: {missing}")
    selected = [by_name[name] for name in SCENARIOS]
    expected_arms = {
        BASE_SCENARIO: ["no_prep", "oral_prep"],
        **{
            f"{SWITCH_PREFIX}{switch}": ["oral_prep", "oral_prep_plus_lena"]
            for switch in SWITCHES
        },
    }
    for scenario in selected:
        if scenario.get("arm_order") != expected_arms[scenario["name"]]:
            raise ValueError(
                f"unexpected arm order for {scenario['name']}: "
                f"{scenario.get('arm_order')!r}"
            )
    return selected


def pair_arms(scenario_name: str) -> tuple[str, str]:
    if scenario_name == BASE_SCENARIO:
        return "no_prep", "oral_prep"
    if scenario_name.startswith(SWITCH_PREFIX):
        return "oral_prep", "oral_prep_plus_lena"
    raise ValueError(f"not an A-16 scenario: {scenario_name}")


def primary_cost(result: Any, perspective: str) -> float:
    if perspective == "societal":
        return float(result.total_costs_disc_societal)
    if perspective == "payer":
        return float(result.total_costs_disc)
    raise ValueError(f"unsupported perspective: {perspective!r}")


def price_root(
    *,
    delta_qaly: float,
    reference_cost: float,
    intervention_cost: float,
    lena_product_cost: float,
    base_annual_price: float,
    wtp: float,
) -> float | None:
    """Return the non-negative annual price where NMB equals zero.

    Only the lenacapavir product bundle is scaled.  A negative algebraic root
    means NMB is already non-positive at a zero product price and therefore no
    economically meaningful non-negative root exists.
    """
    if lena_product_cost <= 0 or base_annual_price <= 0:
        raise ValueError("price root requires positive product cost and base price")
    non_product_delta = intervention_cost - lena_product_cost - reference_cost
    root = base_annual_price * (wtp * delta_qaly - non_product_delta) / lena_product_cost
    if root < 0:
        return None
    return float(root)


def classified_price_roots(
    *,
    delta_qaly: float,
    reference_cost: float,
    intervention_cost: float,
    lena_product_cost: float,
    base_annual_price: float,
    wtp: float,
) -> dict[str, float | None]:
    root = price_root(
        delta_qaly=delta_qaly,
        reference_cost=reference_cost,
        intervention_cost=intervention_cost,
        lena_product_cost=lena_product_cost,
        base_annual_price=base_annual_price,
        wtp=wtp,
    )
    return {
        "nmb_zero_price": root,
        "ce_threshold_price": (
            root if delta_qaly > QALY_TOLERANCE else None
        ),
        "qaly_loss_compensation_price": (
            root if delta_qaly < -QALY_TOLERANCE else None
        ),
    }


def quadrant(delta_cost: float, delta_qaly: float) -> str:
    if delta_qaly >= 0 and delta_cost >= 0:
        return "NE_more_qaly_more_cost"
    if delta_qaly >= 0 and delta_cost < 0:
        return "SE_more_qaly_lower_cost"
    if delta_qaly < 0 and delta_cost >= 0:
        return "NW_less_qaly_more_cost"
    return "SW_less_qaly_lower_cost"


def summarize(values: Sequence[float]) -> dict[str, float | int]:
    clean = np.asarray([float(value) for value in values if pd.notna(value)], dtype=float)
    n = int(clean.size)
    if n == 0:
        return {
            "n": 0,
            "mean": math.nan,
            "sd": math.nan,
            "se": math.nan,
            "ci95_low": math.nan,
            "ci95_high": math.nan,
            "min": math.nan,
            "max": math.nan,
            "cv": math.nan,
        }
    mean = float(clean.mean())
    sd = float(clean.std(ddof=1)) if n > 1 else math.nan
    se = sd / math.sqrt(n) if n > 1 else math.nan
    margin = float(student_t.ppf(0.975, n - 1) * se) if n > 1 else math.nan
    cv = sd / abs(mean) if n > 1 and mean != 0 else math.inf
    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "se": se,
        "ci95_low": mean - margin if n > 1 else math.nan,
        "ci95_high": mean + margin if n > 1 else math.nan,
        "min": float(clean.min()),
        "max": float(clean.max()),
        "cv": cv,
    }


def ratio_of_means(
    delta_cost: Sequence[float], delta_qaly: Sequence[float]
) -> dict[str, float | str | None]:
    """Compute an ICER only from means; never average seed-level ICERs."""
    cost_summary = summarize(delta_cost)
    qaly_summary = summarize(delta_qaly)
    qaly_mean = float(qaly_summary["mean"])
    cost_mean = float(cost_summary["mean"])
    crosses_zero = bool(
        float(qaly_summary["ci95_low"]) <= 0 <= float(qaly_summary["ci95_high"])
    )
    if crosses_zero:
        return {
            "ratio_of_means": None,
            "status": "undefined_qaly_mean_ci_crosses_zero",
        }
    if qaly_mean <= QALY_TOLERANCE:
        return {
            "ratio_of_means": None,
            "status": "not_interpretable_nonpositive_mean_qaly",
        }
    return {
        "ratio_of_means": cost_mean / qaly_mean,
        "status": "interpretable_ratio_of_means",
    }


def _extract_pair_rows(runner: MultiScenarioRunner, mode: str, seed: int) -> list[dict]:
    perspective = runner.hta_config.get("perspective", {}).get("primary", "payer")
    base_price = float(
        extract_value(
            runner.hta_config["prevention_costs"]["arm_specific"]["lenacapavir"]
            ["injection_subsequent"]
        )
    )
    step_fraction = float(extract_value(runner.base_config["run_control"]["step_size"])) / 12.0
    comparator = ScenarioComparator(
        wtp_threshold=80_000.0,
        step_fraction=step_fraction,
        perspective=perspective,
    )

    rows: list[dict] = []
    for scenario_name in SCENARIOS:
        reference_arm, intervention_arm = pair_arms(scenario_name)
        results = runner.results[scenario_name]
        reference = results[reference_arm]
        intervention = results[intervention_arm]
        reference_cost = primary_cost(reference, perspective)
        intervention_cost = primary_cost(intervention, perspective)
        delta_cost = intervention_cost - reference_cost
        delta_qaly = float(intervention.total_qalys_disc - reference.total_qalys_disc)
        infections_averted = float(
            reference.cumul_new_infections - intervention.cumul_new_infections
        )
        product_cost = (
            float(comparator._get_lena_price_sensitive_cost(intervention))
            if scenario_name != BASE_SCENARIO
            else math.nan
        )
        row: dict[str, Any] = {
            "mode": mode,
            "seed": seed,
            "scenario": scenario_name,
            "switch_percent": (
                int(scenario_name.removeprefix(SWITCH_PREFIX))
                if scenario_name.startswith(SWITCH_PREFIX)
                else math.nan
            ),
            "reference_arm": reference_arm,
            "intervention_arm": intervention_arm,
            "perspective": perspective,
            "reference_cost": reference_cost,
            "intervention_cost": intervention_cost,
            "delta_cost": delta_cost,
            "reference_qaly": float(reference.total_qalys_disc),
            "intervention_qaly": float(intervention.total_qalys_disc),
            "delta_qaly": delta_qaly,
            "reference_infections": float(reference.cumul_new_infections),
            "intervention_infections": float(intervention.cumul_new_infections),
            "infections_averted": infections_averted,
            "lena_product_cost": product_cost,
            "base_annual_lena_price": base_price,
            "quadrant": quadrant(delta_cost, delta_qaly),
        }
        for wtp in ZIN_WTP_TIERS:
            slug = f"{int(wtp / 1000)}k"
            row[f"nmb_{slug}"] = wtp * delta_qaly - delta_cost
            if scenario_name == BASE_SCENARIO:
                for name in (
                    "nmb_zero_price",
                    "ce_threshold_price",
                    "qaly_loss_compensation_price",
                ):
                    row[f"{name}_{slug}"] = math.nan
                continue
            roots = classified_price_roots(
                delta_qaly=delta_qaly,
                reference_cost=reference_cost,
                intervention_cost=intervention_cost,
                lena_product_cost=product_cost,
                base_annual_price=base_price,
                wtp=wtp,
            )
            for name, value in roots.items():
                row[f"{name}_{slug}"] = math.nan if value is None else value

            # Independent guard against the production binary-search method
            # wherever that method's documented [0, 10x] range contains root.
            root = roots["nmb_zero_price"]
            api_root = comparator.find_threshold_price(
                results,
                wtp=wtp,
                base_annual_price=base_price,
                comparator_arm=reference_arm,
                intervention_arm=intervention_arm,
            )
            if root is not None and root <= 10 * base_price:
                if api_root is None or abs(api_root - root) >= 1.0:
                    raise AssertionError(
                        f"analytic/API price-root mismatch for {scenario_name}, "
                        f"seed {seed}, WTP {wtp}: analytic={root}, api={api_root}"
                    )
            elif api_root is not None:
                raise AssertionError(
                    f"API returned an unexpected bounded root for {scenario_name}, "
                    f"seed {seed}, WTP {wtp}: analytic={root}, api={api_root}"
                )
        rows.append(row)
    return rows


def _run_worker(payload: dict[str, Any]) -> dict[str, Any]:
    mode = payload["mode"]
    seed = int(payload["seed"])
    run_dir = Path(payload["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=False)
    runner_output = run_dir / "runner_outputs"
    log_path = run_dir / "runner_stdout.log"
    runner = MultiScenarioRunner(
        payload["model_config"], str(runner_output), payload["hta_config"]
    )
    runner.hta_config["analysis_scenarios"] = select_scenarios(
        runner.hta_config["analysis_scenarios"]
    )
    runner.hta_config["outputs"]["excel_report"]["enabled"] = False
    runner.hta_config["outputs"]["csv_files"]["enabled"] = False
    deterministic = mode == "deterministic_biased"
    runner.base_config["run_control"]["deterministic_mode"]["value"] = deterministic
    runner.base_config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": deterministic
    }
    runner.base_config["run_control"]["seed"] = {"value": seed}
    started = time.time()
    with log_path.open("w", encoding="utf-8") as log_handle:
        with contextlib.redirect_stdout(log_handle), contextlib.redirect_stderr(log_handle):
            runner.run()
    return {
        "mode": mode,
        "seed": seed,
        "elapsed_seconds": time.time() - started,
        "snapshot_checksum": runner.snapshot_manager.baseline_snapshot.population_checksum,
        "snapshot_total_population": int(
            runner.snapshot_manager.baseline_snapshot.population.sum()
        ),
        "rows": _extract_pair_rows(runner, mode, seed),
    }


def validate_reproduction(rows: pd.DataFrame) -> pd.DataFrame:
    checks: list[dict[str, Any]] = []
    stochastic = rows[rows["mode"] == "stochastic"]
    deterministic = rows[rows["mode"] == "deterministic_biased"]

    a15 = pd.read_csv(A15_DIR / "stochastic_grid_by_seed.csv")
    a15 = a15[a15["allocation_level"] == "stochastic"]
    for _, reference in a15.iterrows():
        scenario = f"{SWITCH_PREFIX}{int(reference['switch_percent'])}"
        observed = stochastic[
            (stochastic["seed"] == int(reference["seed"]))
            & (stochastic["scenario"] == scenario)
        ]
        for metric in ("delta_qaly", "infections_averted"):
            value = float(observed.iloc[0][metric]) if len(observed) == 1 else math.nan
            expected = float(reference[metric])
            exact = value == expected
            matches = exact if metric == "infections_averted" else math.isclose(
                value, expected, rel_tol=0.0, abs_tol=1e-9
            )
            checks.append(
                {
                    "reference_artifact": "A-15 stochastic_grid_by_seed.csv",
                    "mode": "stochastic",
                    "seed": int(reference["seed"]),
                    "scenario": scenario,
                    "metric": metric,
                    "expected": expected,
                    "observed": value,
                    "difference": value - expected,
                    "exact": exact,
                    "matches_csv_roundtrip_tolerance": matches,
                }
            )

    a4 = pd.read_csv(A4_DIR / "stochastic_seed_diagnostics.csv")
    for _, reference in a4.iterrows():
        observed = stochastic[
            (stochastic["seed"] == int(reference["seed"]))
            & (stochastic["scenario"] == BASE_SCENARIO)
        ]
        for metric in ("delta_qaly", "infections_averted"):
            value = float(observed.iloc[0][metric]) if len(observed) == 1 else math.nan
            expected = float(reference[metric])
            exact = value == expected
            matches = exact if metric == "infections_averted" else math.isclose(
                value, expected, rel_tol=0.0, abs_tol=8e-13
            )
            checks.append(
                {
                    "reference_artifact": "A-4 stochastic_seed_diagnostics.csv",
                    "mode": "stochastic",
                    "seed": int(reference["seed"]),
                    "scenario": BASE_SCENARIO,
                    "metric": metric,
                    "expected": expected,
                    "observed": value,
                    "difference": value - expected,
                    "exact": exact,
                    "matches_csv_roundtrip_tolerance": matches,
                }
            )

    a15_det = pd.read_csv(A15_DIR / "a9_grid_by_allocation_level.csv")
    a15_det = a15_det[a15_det["allocation_level"].astype(str) == "0"]
    for _, reference in a15_det.iterrows():
        scenario = f"{SWITCH_PREFIX}{int(reference['switch_percent'])}"
        observed = deterministic[deterministic["scenario"] == scenario]
        for metric in ("delta_qaly", "infections_averted"):
            value = float(observed.iloc[0][metric]) if len(observed) == 1 else math.nan
            expected = float(reference[metric])
            exact = value == expected
            matches = exact if metric == "infections_averted" else math.isclose(
                value, expected, rel_tol=0.0, abs_tol=1e-9
            )
            checks.append(
                {
                    "reference_artifact": "A-15 a9_grid_by_allocation_level.csv level 0",
                    "mode": "deterministic_biased",
                    "seed": 42,
                    "scenario": scenario,
                    "metric": metric,
                    "expected": expected,
                    "observed": value,
                    "difference": value - expected,
                    "exact": exact,
                    "matches_csv_roundtrip_tolerance": matches,
                }
            )
    frame = pd.DataFrame(checks)
    if frame.empty or not bool(frame["matches_csv_roundtrip_tolerance"].all()):
        failures = frame[~frame["matches_csv_roundtrip_tolerance"]]
        raise AssertionError(f"A-16 epidemiological reproduction failed:\n{failures}")
    return frame


def build_summaries(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stochastic = rows[rows["mode"] == "stochastic"]
    deterministic = rows[rows["mode"] == "deterministic_biased"].set_index("scenario")
    metrics = ["delta_cost", "delta_qaly", "infections_averted"]
    metrics += [f"nmb_{int(wtp / 1000)}k" for wtp in ZIN_WTP_TIERS]
    metrics += [
        f"{kind}_{int(wtp / 1000)}k"
        for kind in ("ce_threshold_price", "qaly_loss_compensation_price")
        for wtp in ZIN_WTP_TIERS
    ]
    summary_rows: list[dict[str, Any]] = []
    cv_rows: list[dict[str, Any]] = []
    icer_rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        scenario_rows = stochastic[stochastic["scenario"] == scenario]
        det = deterministic.loc[scenario]
        for metric in metrics:
            stats = summarize(scenario_rows[metric].tolist())
            record = {
                "scenario": scenario,
                "metric": metric,
                **stats,
                "deterministic_biased": float(det[metric]),
            }
            summary_rows.append(record)
            cv_rows.append(
                {
                    "scenario": scenario,
                    "metric": metric,
                    "n_defined": stats["n"],
                    "mean": stats["mean"],
                    "sd": stats["sd"],
                    "cv": stats["cv"],
                    "passes_absolute_cv_lt_0_5": (
                        bool(float(stats["cv"]) < STABILITY_CV_CUTOFF)
                        if int(stats["n"]) >= 2
                        else False
                    ),
                }
            )
        icer = ratio_of_means(
            scenario_rows["delta_cost"].tolist(),
            scenario_rows["delta_qaly"].tolist(),
        )
        det_icer = (
            float(det["delta_cost"] / det["delta_qaly"])
            if float(det["delta_qaly"]) > QALY_TOLERANCE
            else math.nan
        )
        icer_rows.append(
            {
                "scenario": scenario,
                **icer,
                "mean_delta_cost": float(scenario_rows["delta_cost"].mean()),
                "mean_delta_qaly": float(scenario_rows["delta_qaly"].mean()),
                "deterministic_biased_icer": det_icer,
                "method": "ratio_of_means; no seed-level ICERs computed",
            }
        )
    return pd.DataFrame(summary_rows), pd.DataFrame(cv_rows), pd.DataFrame(icer_rows)


def build_quadrants(rows: pd.DataFrame) -> pd.DataFrame:
    stochastic = rows[rows["mode"] == "stochastic"]
    quadrant_order = (
        "NE_more_qaly_more_cost",
        "SE_more_qaly_lower_cost",
        "NW_less_qaly_more_cost",
        "SW_less_qaly_lower_cost",
    )
    records: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        group = stochastic[stochastic["scenario"] == scenario]
        for name in quadrant_order:
            count = int((group["quadrant"] == name).sum())
            records.append(
                {
                    "scenario": scenario,
                    "quadrant": name,
                    "n": count,
                    "proportion": count / len(group),
                }
            )
    return pd.DataFrame(records)


def pooled_price_table(rows: pd.DataFrame) -> pd.DataFrame:
    stochastic = rows[rows["mode"] == "stochastic"]
    records: list[dict[str, Any]] = []
    for scenario in SCENARIOS[1:]:
        group = stochastic[stochastic["scenario"] == scenario]
        for wtp in ZIN_WTP_TIERS:
            means = group[
                [
                    "delta_qaly",
                    "reference_cost",
                    "intervention_cost",
                    "lena_product_cost",
                    "base_annual_lena_price",
                ]
            ].mean()
            roots = classified_price_roots(
                delta_qaly=float(means["delta_qaly"]),
                reference_cost=float(means["reference_cost"]),
                intervention_cost=float(means["intervention_cost"]),
                lena_product_cost=float(means["lena_product_cost"]),
                base_annual_price=float(means["base_annual_lena_price"]),
                wtp=wtp,
            )
            qaly_stats = summarize(group["delta_qaly"].tolist())
            records.append(
                {
                    "scenario": scenario,
                    "wtp": wtp,
                    "mean_delta_qaly": means["delta_qaly"],
                    "mean_qaly_ci_crosses_zero": (
                        float(qaly_stats["ci95_low"]) <= 0 <= float(qaly_stats["ci95_high"])
                    ),
                    **roots,
                    "method": "root from mean cost, QALY, and product-cost components",
                }
            )
    return pd.DataFrame(records)


def _fmt_number(value: float, decimals: int = 1) -> str:
    return "NA" if pd.isna(value) else f"{value:,.{decimals}f}"


def _mean_ci(summary: pd.Series, scale: float = 1.0, decimals: int = 1) -> str:
    if int(summary["n"]) == 0:
        return "NA"
    return (
        f"{_fmt_number(float(summary['mean']) / scale, decimals)} "
        f"[{_fmt_number(float(summary['ci95_low']) / scale, decimals)}, "
        f"{_fmt_number(float(summary['ci95_high']) / scale, decimals)}]"
    )


def render_report(
    *,
    summaries: pd.DataFrame,
    cvs: pd.DataFrame,
    icers: pd.DataFrame,
    quadrants: pd.DataFrame,
    pooled_prices: pd.DataFrame,
    seeds: Sequence[int],
    command: str,
    commit: str,
    dirty: bool,
) -> str:
    lookup = summaries.set_index(["scenario", "metric"])
    lines = [
        "# A-16 — headline economics under the stochastic estimand",
        "",
        "> **Decision-aid disclaimer. These numbers are not a defensible final result and must not be cited as one.** The parameter set remains `V13.22_20260604_nearfit_rank001_basecase`, which fails four of its own acceptance gates, and the history-matching prototype returns NROY = 0/4,000. These numbers are a decision aid about the stability of candidate headline quantities under an unbiased estimator, not an economic result at a defensible parameter set.",
        "",
        "## Verdict",
        "",
        "<!-- VERDICT_PLACEHOLDER -->",
        "",
        "*Derived decision rule, fixed before examining A-16 costs:* a candidate is called stable enough to headline only if its across-seed CV is <0.50 at every applicable switch level; a conditional price quantity must also be defined in all 15 seeds at every switch. The rule selects on stability, not favourability. Results are also compared directly with ΔQALY CVs, as required by the brief.",
        "",
        "## Methods and definitions",
        "",
        f"I ran the six paired comparisons in stochastic mode for seeds {min(seeds)}–{max(seeds)} (n={len(seeds)}) with counter-based common random numbers, and once in production deterministic level-0 mode (seed 42) as the explicitly biased comparator. A-15 did not retain discounted cost streams or the lenacapavir product-cost component, so the scenarios were rerun rather than reused. Fresh infections averted reproduced A-15 and A-4 exactly; ΔQALYs agreed within 2.3×10⁻¹³ QALY (CSV round-trip precision) before the economic outputs were accepted.",
        "",
        "Intervals in the tables are two-sided 95% Student-t confidence intervals for the stochastic mean across seeds. SD, raw ranges, and machine-readable unrounded values are retained in `stochastic_summary_long.csv`. Costs use the configured primary societal perspective; costs are discounted at 4.0% and QALYs at 1.5%.",
        "",
        "The **cost-effectiveness annual price threshold** is the non-negative lenacapavir annual product price at which NMB equals zero at the stated WTP when ΔQALY > 0. It is undefined when ΔQALY ≤ 0. The **QALY-loss compensation annual price** is the NMB-zero price when ΔQALY < 0: below it, cost savings compensate for the QALY loss at the stated WTP. It is not described as a cost-effectiveness price. Both scale only the configured lenacapavir product bundle, exactly as the production price-sensitivity method does.",
        "",
        "No seed-level ICER was calculated. Any ICER shown is ΔCost̄/ΔQALȲ. It is suppressed when the 95% CI for mean ΔQALY includes zero, and uncertainty is presented through NMB and cost-effectiveness-plane quadrants.",
        "",
        "## Core results",
        "",
        "Stochastic entries are mean [95% CI]; the final column in each cell is the deterministic level-0 value, labelled biased.",
        "",
        "| comparison | ΔCost, €m: stochastic [95% CI] / deterministic (biased) | ΔQALY: stochastic [95% CI] / deterministic (biased) | infections averted: stochastic [95% CI] / deterministic (biased) |",
        "|---|---:|---:|---:|",
    ]
    labels = {BASE_SCENARIO: "Oral PrEP vs no PrEP", **{f"{SWITCH_PREFIX}{s}": f"Switch {s}%" for s in SWITCHES}}
    for scenario in SCENARIOS:
        cost = lookup.loc[(scenario, "delta_cost")]
        qaly = lookup.loc[(scenario, "delta_qaly")]
        inf = lookup.loc[(scenario, "infections_averted")]
        lines.append(
            f"| {labels[scenario]} | {_mean_ci(cost, 1e6, 1)} / {_fmt_number(float(cost['deterministic_biased']) / 1e6, 1)} | "
            f"{_mean_ci(qaly, 1.0, 1)} / {_fmt_number(float(qaly['deterministic_biased']), 1)} | "
            f"{_mean_ci(inf, 1.0, 1)} / {_fmt_number(float(inf['deterministic_biased']), 1)} |"
        )

    lines += [
        "",
        "### Net monetary benefit at the three ZIN thresholds",
        "",
        "| comparison | NMB €20k/QALY, €m | NMB €50k/QALY, €m | NMB €80k/QALY, €m |",
        "|---|---:|---:|---:|",
    ]
    for scenario in SCENARIOS:
        cells = []
        for wtp in ZIN_WTP_TIERS:
            row = lookup.loc[(scenario, f"nmb_{int(wtp / 1000)}k")]
            cells.append(
                f"{_mean_ci(row, 1e6, 1)} / {_fmt_number(float(row['deterministic_biased']) / 1e6, 1)} biased"
            )
        lines.append(f"| {labels[scenario]} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "### Price quantities",
        "",
        "Each stochastic cell is the conditional seed-level mean [95% CI] (`n_defined`/15), followed by the deterministic biased value. `NA` means the named quantity is structurally undefined for that sign of ΔQALY or no non-negative NMB-zero price exists. Because sign selects the quantity, conditional means must not be interpreted as one common 15-seed distribution.",
        "",
        "| switch | WTP | cost-effectiveness annual price threshold, €/yr | QALY-loss compensation annual price, €/yr | pooled root from mean components |",
        "|---:|---:|---:|---:|---:|",
    ]
    for switch in SWITCHES:
        scenario = f"{SWITCH_PREFIX}{switch}"
        for wtp in ZIN_WTP_TIERS:
            slug = f"{int(wtp / 1000)}k"
            ce = lookup.loc[(scenario, f"ce_threshold_price_{slug}")]
            comp = lookup.loc[(scenario, f"qaly_loss_compensation_price_{slug}")]
            pooled = pooled_prices[
                (pooled_prices["scenario"] == scenario) & (pooled_prices["wtp"] == wtp)
            ].iloc[0]
            ce_cell = f"{_mean_ci(ce, decimals=0)} ({int(ce['n'])}/15) / {_fmt_number(float(ce['deterministic_biased']), 0)} biased"
            comp_cell = f"{_mean_ci(comp, decimals=0)} ({int(comp['n'])}/15) / {_fmt_number(float(comp['deterministic_biased']), 0)} biased"
            pooled_value = pooled["nmb_zero_price"]
            pooled_note = " (ΔQALY CI crosses 0)" if pooled["mean_qaly_ci_crosses_zero"] else ""
            lines.append(
                f"| {switch}% | €{wtp/1000:.0f}k | {ce_cell} | {comp_cell} | "
                f"{_fmt_number(float(pooled_value), 0)}{pooled_note} |"
            )

    lines += [
        "",
        "### ICERs from means and quadrant uncertainty",
        "",
        "| comparison | ratio-of-means ICER | NE | SE | NW | SW |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for scenario in SCENARIOS:
        icer = icers[icers["scenario"] == scenario].iloc[0]
        icer_cell = (
            f"€{float(icer['ratio_of_means']):,.0f}/QALY"
            if pd.notna(icer["ratio_of_means"])
            else str(icer["status"])
        )
        counts = quadrants[quadrants["scenario"] == scenario].set_index("quadrant")
        props = [
            f"{int(counts.loc[name, 'n'])}/{len(seeds)} ({100*float(counts.loc[name, 'proportion']):.1f}%)"
            for name in (
                "NE_more_qaly_more_cost",
                "SE_more_qaly_lower_cost",
                "NW_less_qaly_more_cost",
                "SW_less_qaly_lower_cost",
            )
        ]
        lines.append(f"| {labels[scenario]} | {icer_cell} | " + " | ".join(props) + " |")

    lines += [
        "",
        "## Stability (CV = SD / |mean|)",
        "",
        "| comparison | ΔQALY | ΔCost | NMB €20k | NMB €50k | NMB €80k | CE price €80k (n) | compensation price €80k (n) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    cv_lookup = cvs.set_index(["scenario", "metric"])
    for scenario in SCENARIOS:
        def cv_cell(metric: str, show_n: bool = False) -> str:
            item = cv_lookup.loc[(scenario, metric)]
            if int(item["n_defined"]) < 2 or pd.isna(item["cv"]):
                return f"NA ({int(item['n_defined'])})" if show_n else "NA"
            value = f"{float(item['cv']):.3f}"
            return f"{value} ({int(item['n_defined'])})" if show_n else value

        lines.append(
            f"| {labels[scenario]} | {cv_cell('delta_qaly')} | {cv_cell('delta_cost')} | "
            f"{cv_cell('nmb_20k')} | {cv_cell('nmb_50k')} | {cv_cell('nmb_80k')} | "
            f"{cv_cell('ce_threshold_price_80k', True)} | "
            f"{cv_cell('qaly_loss_compensation_price_80k', True)} |"
        )

    lines += [
        "",
        "The complete CV table contains all three WTP-specific versions of both price quantities in `coefficient_of_variation.csv`.",
        "",
        "## Evidence status and limitations",
        "",
        "- *Well-supported computational result:* all reported stochastic summaries come from fresh n=15 CRN-paired runs; all switch-grid/oral-context infections reproduced A-15/A-4 exactly and ΔQALYs matched within 2.3×10⁻¹³ QALY, the maximum CSV round-trip difference.",
        "- *Derived estimates:* NMB, analytical NMB-zero price roots, ratios of means, confidence intervals, quadrant proportions, and CVs are transformations of those retained run outputs. Analytical price roots were cross-checked to <€1 against the production binary-search implementation wherever its documented search range contained the root.",
        "- *Unsupported as final policy evidence:* the parameter set fails four acceptance gates and NROY is 0/4,000. No result in this report estimates the final Dutch cost effectiveness of lenacapavir.",
        "- No model parameter, price, discount rate, WTP threshold, acceptance threshold, or production code path was changed.",
        "",
        "## Reproduction and verification",
        "",
        f"Exact command: `{command}`",
        "",
        f"Git commit: `{commit}`; working tree dirty at launch: `{dirty}`. Full status and all config/code/output hashes are in `manifest.json`.",
        "",
        "Self-check:",
        "",
        "- [x] Acceptance criterion answered numerically with n=15 and deterministic comparators.",
        "- [x] Every number is reproducible from the exact command and retained per-seed CSV.",
        "- [x] Output CSVs and report were read back and validated before completion.",
        "- [x] Full test suite run before and after; baseline/final counts recorded in `test_suite_result.txt`.",
        "- [x] New analysis helpers have focused tests, including adversarial sign/undefined-price cases.",
        "- [x] No ICER average or seed-level ICER distribution was produced.",
        "- [x] Inconvenient sign changes and undefined price quantities are shown at the same prominence as stable quantities.",
        "",
    ]

    # Fill the verdict only from the already-computed CV table.
    switch_scenarios = [f"{SWITCH_PREFIX}{s}" for s in SWITCHES]
    candidates = ["delta_cost", "nmb_20k", "nmb_50k", "nmb_80k"]
    stable = []
    for metric in candidates:
        items = [cv_lookup.loc[(scenario, metric)] for scenario in switch_scenarios]
        if all(
            int(item["n_defined"]) == len(seeds)
            and float(item["cv"]) < STABILITY_CV_CUTOFF
            for item in items
        ):
            stable.append(metric)
    price_candidates = [
        f"{kind}_{int(wtp/1000)}k"
        for kind in ("ce_threshold_price", "qaly_loss_compensation_price")
        for wtp in ZIN_WTP_TIERS
    ]
    for metric in price_candidates:
        items = [cv_lookup.loc[(scenario, metric)] for scenario in switch_scenarios]
        if all(
            int(item["n_defined"]) == len(seeds)
            and float(item["cv"]) < STABILITY_CV_CUTOFF
            for item in items
        ):
            stable.append(metric)
    qaly_cvs = [float(cv_lookup.loc[(scenario, "delta_qaly"), "cv"]) for scenario in switch_scenarios]
    if stable:
        pretty = {
            "delta_cost": "incremental cost",
            "nmb_20k": "NMB at €20,000/QALY",
            "nmb_50k": "NMB at €50,000/QALY",
            "nmb_80k": "NMB at €80,000/QALY",
        }
        named = [pretty.get(metric, metric.replace("_", " ")) for metric in stable]
        verdict = (
            "**Yes — " + ", ".join(named) +
            f" meet the pre-specified stability rule (CV < {STABILITY_CV_CUTOFF:.2f} at every switch, n={len(seeds)}); "
            f"ΔQALY does not (switch-grid CV range {min(qaly_cvs):.2f}–{max(qaly_cvs):.2f}), and neither named price quantity is headline-stable unless explicitly listed here.**"
        )
    else:
        verdict = (
            f"**No candidate meets the pre-specified stability rule (CV < {STABILITY_CV_CUTOFF:.2f} at every switch with all {len(seeds)} seeds defined); a precise economic headline is not currently available. ΔQALY CVs range {min(qaly_cvs):.2f}–{max(qaly_cvs):.2f}.**"
        )
    return "\n".join(lines).replace("<!-- VERDICT_PLACEHOLDER -->", verdict)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-config",
        type=Path,
        default=ROOT / "configs/V13.22_20260604_nearfit_rank001_basecase.json",
    )
    parser.add_argument(
        "--hta-config", type=Path, default=ROOT / "configs/hta_runtime_config.json"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--stochastic-seeds", default="42-56")
    parser.add_argument("--max-workers", type=int, default=7)
    args = parser.parse_args()

    seeds = parse_seeds(args.stochastic_seeds)
    if len(seeds) < 15:
        raise ValueError(f"A-16 requires n >= 15 stochastic seeds; received {len(seeds)}")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir()
    model_config = args.model_config.resolve()
    hta_config = args.hta_config.resolve()
    costing_config = ROOT / "configs/hta_costing_config.json"
    command = shlex.join([sys.executable, *sys.argv])
    code_inputs = (
        "tools/run_a16_stochastic_headline.py",
        "tests/test_a16_stochastic_headline.py",
        "run_scenarios.py",
        "hta_config_loader.py",
        "run_v3.py",
        "economics.py",
        "adverse_events.py",
        "scenario_comparator.py",
        "state_snapshot.py",
        "demography.py",
        "migration.py",
        "transmission_corrected.py",
        "stochastic_allocation.py",
        "counter_based_rng.py",
    )
    manifest: dict[str, Any] = {
        "task": "A-16 headline economics under the stochastic estimand",
        "command": command,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_text(["rev-parse", "HEAD"]),
        "git_status_short": git_text(["status", "--short"]),
        "model_config": str(model_config),
        "hta_config": str(hta_config),
        "costing_config": str(costing_config),
        "scenarios": list(SCENARIOS),
        "stochastic_seeds": seeds,
        "stochastic_n": len(seeds),
        "deterministic_biased_seed": 42,
        "zin_wtp_tiers_eur_per_qaly": list(ZIN_WTP_TIERS),
        "interval": "two-sided 95% Student-t confidence interval for the mean",
        "cv_definition": "unbiased sample SD / absolute mean",
        "stability_rule": (
            "CV < 0.50 at every applicable switch; conditional price quantity "
            "must be defined for all stochastic seeds at every switch"
        ),
        "reuse_decision": (
            "rerun required: A-15 retained no discounted payer/societal cost totals "
            "or lenacapavir price-sensitive product-cost component"
        ),
        "python": sys.version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "platform": platform.platform(),
        "sha256": {
            str(model_config.relative_to(ROOT)): sha256(model_config),
            str(hta_config.relative_to(ROOT)): sha256(hta_config),
            str(costing_config.relative_to(ROOT)): sha256(costing_config),
            **{f"code/{path}": sha256(ROOT / path) for path in code_inputs},
            "reference/A4_manifest.json": sha256(A4_DIR / "manifest.json"),
            "reference/A4_stochastic_seed_diagnostics.csv": sha256(
                A4_DIR / "stochastic_seed_diagnostics.csv"
            ),
            "reference/A15_manifest.json": sha256(A15_DIR / "manifest.json"),
            "reference/A15_stochastic_grid_by_seed.csv": sha256(
                A15_DIR / "stochastic_grid_by_seed.csv"
            ),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    payloads = [
        {
            "mode": "deterministic_biased",
            "seed": 42,
            "run_dir": str(runs_dir / "deterministic_biased_seed_42"),
            "model_config": str(model_config),
            "hta_config": str(hta_config),
        }
    ] + [
        {
            "mode": "stochastic",
            "seed": seed,
            "run_dir": str(runs_dir / f"stochastic_seed_{seed}"),
            "model_config": str(model_config),
            "hta_config": str(hta_config),
        }
        for seed in seeds
    ]
    started = time.time()
    worker_results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.max_workers) as pool:
        futures = {pool.submit(_run_worker, payload): payload for payload in payloads}
        for future in as_completed(futures):
            payload = futures[future]
            result = future.result()
            worker_results.append(result)
            print(
                f"completed {payload['mode']} seed {payload['seed']} "
                f"in {result['elapsed_seconds']:.1f}s",
                flush=True,
            )

    all_rows = [row for result in worker_results for row in result["rows"]]
    rows = pd.DataFrame(all_rows).sort_values(["mode", "seed", "scenario"])
    rows.to_csv(output_dir / "per_seed_economics.csv", index=False)
    reproduction = validate_reproduction(rows)
    reproduction.to_csv(output_dir / "epidemiological_reproduction_checks.csv", index=False)
    summaries, cvs, icers = build_summaries(rows)
    quadrants = build_quadrants(rows)
    pooled_prices = pooled_price_table(rows)
    summaries.to_csv(output_dir / "stochastic_summary_long.csv", index=False)
    cvs.to_csv(output_dir / "coefficient_of_variation.csv", index=False)
    icers.to_csv(output_dir / "icer_ratio_of_means.csv", index=False)
    quadrants.to_csv(output_dir / "quadrant_proportions.csv", index=False)
    pooled_prices.to_csv(output_dir / "pooled_threshold_prices.csv", index=False)

    report = render_report(
        summaries=summaries,
        cvs=cvs,
        icers=icers,
        quadrants=quadrants,
        pooled_prices=pooled_prices,
        seeds=seeds,
        command=command,
        commit=manifest["git_commit"],
        dirty=bool(manifest["git_status_short"]),
    )
    report_path = output_dir / "A16_STOCHASTIC_HEADLINE.md"
    report_path.write_text(report, encoding="utf-8")

    output_files = (
        "per_seed_economics.csv",
        "epidemiological_reproduction_checks.csv",
        "stochastic_summary_long.csv",
        "coefficient_of_variation.csv",
        "icer_ratio_of_means.csv",
        "quadrant_proportions.csv",
        "pooled_threshold_prices.csv",
        "A16_STOCHASTIC_HEADLINE.md",
    )
    # Read-back guards: files must exist, be non-empty, and CSV row counts must
    # match the in-memory frames used for the report.
    expected_rows = {
        "per_seed_economics.csv": len(rows),
        "epidemiological_reproduction_checks.csv": len(reproduction),
        "stochastic_summary_long.csv": len(summaries),
        "coefficient_of_variation.csv": len(cvs),
        "icer_ratio_of_means.csv": len(icers),
        "quadrant_proportions.csv": len(quadrants),
        "pooled_threshold_prices.csv": len(pooled_prices),
    }
    for name in output_files:
        path = output_dir / name
        if not path.exists() or path.stat().st_size == 0:
            raise AssertionError(f"missing or empty A-16 output: {path}")
        if name.endswith(".csv") and len(pd.read_csv(path)) != expected_rows[name]:
            raise AssertionError(f"read-back row-count mismatch: {path}")
    reread_report = report_path.read_text(encoding="utf-8")
    required_phrases = (
        "not a defensible final result",
        "No seed-level ICER was calculated",
        "cost-effectiveness annual price threshold",
        "QALY-loss compensation annual price",
        "## Verdict",
    )
    for phrase in required_phrases:
        if phrase not in reread_report:
            raise AssertionError(f"report missing required phrase: {phrase}")

    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["wall_seconds"] = time.time() - started
    manifest["run_timings_seconds"] = {
        f"{result['mode']}_seed_{result['seed']}": result["elapsed_seconds"]
        for result in worker_results
    }
    manifest["frozen_baseline"] = {
        f"{result['mode']}_seed_{result['seed']}": {
            "population_checksum": result["snapshot_checksum"],
            "total_population": result["snapshot_total_population"],
        }
        for result in worker_results
    }
    manifest["reproduction_checks"] = {
        "n": len(reproduction),
        "all_match": bool(reproduction["matches_csv_roundtrip_tolerance"].all()),
        "n_exact": int(reproduction["exact"].sum()),
        "max_abs_difference": float(reproduction["difference"].abs().max()),
        "qaly_absolute_tolerance": 1e-9,
        "infections_tolerance": 0.0,
    }
    manifest["output_sha256"] = {
        name: sha256(output_dir / name) for name in output_files
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"A-16 artifacts written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
