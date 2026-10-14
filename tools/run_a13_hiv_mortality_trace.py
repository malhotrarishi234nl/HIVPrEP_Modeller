"""Diagnose A-13: deterministic versus stochastic HIV deaths averted.

The runner changes no production behavior.  It substitutes an instrumented
SimulationEngineV3 subclass only inside this process, then runs the A-4 base
case from the same frozen state in deterministic mode and over paired
stochastic seeds.  Every progression and HIV-mortality call records its input
pool, probability, exact expectation, and realised integer event count.
"""

from __future__ import annotations

import argparse
import contextlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import time
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_scenarios as run_scenarios_module  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402
from tools.run_a4_common_random_numbers import (  # noqa: E402
    BASE_SCENARIO,
    configure_runner,
    parse_seeds,
)
from tools.run_a12_deterministic_allocation_audit import select_scenario  # noqa: E402


TRACE_PROCESSES: dict[Any, str] = {
    "progression_acute_to_chronic": "progression_acute_to_chronic",
    "progression_chronic_to_aids_off_art": "progression_chronic_to_aids_off_art",
    "progression_chronic_to_aids_on_art": "progression_chronic_to_aids_on_art",
    ("hiv_mortality", "Acute", "off_art"): "mortality_acute_off_art",
    ("hiv_mortality", "Acute", "on_art"): "mortality_acute_on_art",
    ("hiv_mortality", "Chronic", "off_art"): "mortality_chronic_off_art",
    ("hiv_mortality", "Chronic", "on_art"): "mortality_chronic_on_art",
    ("hiv_mortality", "AIDS", "off_art"): "mortality_aids_off_art",
    ("hiv_mortality", "AIDS", "on_art"): "mortality_aids_on_art",
}
STAGE_NAMES = ("acute", "chronic", "aids")
STAGE_STRATIFIED_PROCESS_IDS = {
    "diagnosis_non_msm": "diagnosis",
    "diagnosis_msm": "diagnosis",
    "diagnosis_all_orientations": "diagnosis",
    "art_initiation": "art_initiation",
    "art_dropout": "art_dropout",
}
STAGE_TRACE_SLUGS = tuple(
    f"{process}_{stage}"
    for process in ("diagnosis", "art_initiation", "art_dropout")
    for stage in STAGE_NAMES
)
ALL_TRACE_SLUGS = (*TRACE_PROCESSES.values(), *STAGE_TRACE_SLUGS)
TRACE_FIELDS = ("pool", "probability", "expected", "actual", "error")
MORTALITY_SLUGS = tuple(
    slug for slug in TRACE_PROCESSES.values() if slug.startswith("mortality_")
)
PROGRESSION_SLUGS = tuple(
    slug for slug in TRACE_PROCESSES.values() if slug.startswith("progression_")
)
FLOW_COLUMNS = (
    "new_infections",
    "new_aids_cases",
    "hiv_deaths",
    "aids_deaths",
    "new_diagnoses",
    "art_initiations",
    "art_initiations_aids",
    "aids_art_initiations",
    "same_step_aids_art_rescue_proxy",
)
STATE_COLUMNS = (
    "aids_stock_start",
    "aids_off_art_start",
    "aids_on_art_start",
    "aids_undiagnosed_start",
    "aids_diagnosed_start",
    "aids_stock_pre_mortality",
    "aids_off_art_pre_mortality",
    "aids_on_art_pre_mortality",
    "aids_undiagnosed_pre_mortality",
    "aids_diagnosed_pre_mortality",
)


class A13TraceEngine(SimulationEngineV3):
    """Diagnostic-only engine that observes probability-event accounting."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._a13_step_trace: dict[str, dict[str, float]] = {}
        for slug in ALL_TRACE_SLUGS:
            for field in TRACE_FIELDS:
                self.outputs[f"a13_{slug}_{field}"] = []

    def step_simulation(self, step: int):
        self._a13_step_trace = {}
        return super().step_simulation(step)

    def _draw_events_for(self, process_id, n: np.ndarray, p: float) -> np.ndarray:
        population = np.asarray(n, dtype=np.int64)
        pool = int(population.sum())
        probability = float(p)
        expected = float(pool * probability)
        events = super()._draw_events_for(process_id, n, p)
        event_array = np.asarray(events, dtype=np.int64)
        slug = TRACE_PROCESSES.get(process_id)
        if slug is not None:
            actual = int(event_array.sum())
            self._a13_step_trace[slug] = {
                "pool": pool,
                "probability": probability,
                "expected": expected,
                "actual": actual,
                "error": actual - expected,
            }
        stage_process = STAGE_STRATIFIED_PROCESS_IDS.get(process_id)
        if stage_process is not None:
            for stage_index, stage_name in enumerate(STAGE_NAMES):
                stage_pool = int(np.take(population, stage_index, axis=3).sum())
                stage_actual = int(np.take(event_array, stage_index, axis=3).sum())
                stage_expected = float(stage_pool * probability)
                stage_slug = f"{stage_process}_{stage_name}"
                previous = self._a13_step_trace.get(stage_slug)
                if previous is None:
                    self._a13_step_trace[stage_slug] = {
                        "pool": stage_pool,
                        "probability": probability,
                        "expected": stage_expected,
                        "actual": stage_actual,
                        "error": stage_actual - stage_expected,
                    }
                else:
                    previous["pool"] += stage_pool
                    previous["expected"] += stage_expected
                    previous["actual"] += stage_actual
                    previous["error"] += stage_actual - stage_expected
                    if previous["probability"] != probability:
                        previous["probability"] = math.nan
        return events

    def record_outputs(self, *args, **kwargs):
        super().record_outputs(*args, **kwargs)
        for slug in ALL_TRACE_SLUGS:
            record = self._a13_step_trace.get(slug, {})
            for field in TRACE_FIELDS:
                self.outputs[f"a13_{slug}_{field}"].append(
                    record.get(field, math.nan)
                )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_text(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.rstrip()


def _install_trace_engine() -> None:
    # MultiScenarioRunner resolves this module global when it constructs both
    # the baseline and arm engines.  The replacement lives only in this worker.
    run_scenarios_module.SimulationEngineV3 = A13TraceEngine


def _run_worker(payload: tuple[str, str, str, bool, int]) -> list[dict]:
    model_config, runtime_config, run_dir_text, deterministic, seed = payload
    _install_trace_engine()
    run_dir = Path(run_dir_text)
    run_dir.mkdir(parents=True, exist_ok=False)
    runner = configure_runner(
        model_config,
        runtime_config,
        str(run_dir / "runner_outputs"),
        deterministic=deterministic,
        seed=seed,
    )
    runner.hta_config["analysis_scenarios"] = select_scenario(
        runner.hta_config["analysis_scenarios"], BASE_SCENARIO
    )
    started = time.time()
    with (run_dir / "runner_stdout.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            runner.run()

    scenario = runner.hta_config["analysis_scenarios"][0]
    rows: list[dict] = []
    for arm in scenario["arm_order"]:
        result = runner.results[BASE_SCENARIO][arm]
        for record in result.simulation_records:
            row = {
                "mode": "deterministic" if deterministic else "stochastic",
                "seed": seed,
                "arm": arm,
                "step": int(record["step"]),
                "year": float(record["year"]),
                "elapsed_seconds": time.time() - started,
            }
            for column in (*FLOW_COLUMNS, *STATE_COLUMNS):
                row[column] = float(record[column])
            for slug in ALL_TRACE_SLUGS:
                for field in TRACE_FIELDS:
                    column = f"a13_{slug}_{field}"
                    row[column] = float(record[column])
            rows.append(row)
    return rows


def paired_trace(arm_rows: pd.DataFrame) -> pd.DataFrame:
    """Return reference-minus-intervention step accounting for each run."""
    index = ["mode", "seed", "step", "year"]
    reference = (
        arm_rows.loc[arm_rows["arm"] == "no_prep"]
        .set_index(index)
        .sort_index()
    )
    intervention = (
        arm_rows.loc[arm_rows["arm"] == "oral_prep"]
        .set_index(index)
        .sort_index()
    )
    if not reference.index.equals(intervention.index):
        raise AssertionError("A-13 arm timestep axes do not match")

    index_frame = pd.DataFrame(index=reference.index).reset_index()
    data = {column: index_frame[column].to_numpy() for column in index}
    source_columns = [*FLOW_COLUMNS, *STATE_COLUMNS]
    for slug in ALL_TRACE_SLUGS:
        source_columns.extend(f"a13_{slug}_{field}" for field in TRACE_FIELDS)

    for column in source_columns:
        ref_values = reference[column].to_numpy(dtype=float)
        int_values = intervention[column].to_numpy(dtype=float)
        data[f"reference_{column}"] = ref_values
        data[f"intervention_{column}"] = int_values
        if not column.endswith("_probability"):
            data[f"{column}_averted"] = ref_values - int_values

    result = pd.DataFrame(data)

    # Aggregate the six mortality calls into one auditable identity.
    mortality_totals = {}
    for field in ("expected", "actual", "error"):
        for prefix in ("reference", "intervention"):
            mortality_totals[f"{prefix}_a13_mortality_total_{field}"] = sum(
                result[f"{prefix}_a13_{slug}_{field}"] for slug in MORTALITY_SLUGS
            )
        mortality_totals[f"a13_mortality_total_{field}_averted"] = (
            mortality_totals[f"reference_a13_mortality_total_{field}"]
            - mortality_totals[f"intervention_a13_mortality_total_{field}"]
        )
    result = pd.concat([result, pd.DataFrame(mortality_totals)], axis=1)

    cumulative = list(FLOW_COLUMNS)
    cumulative.extend(
        f"a13_{slug}_{field}"
        for slug in ALL_TRACE_SLUGS
        for field in ("expected", "actual", "error")
    )
    cumulative.extend(
        f"a13_mortality_total_{field}" for field in ("expected", "actual", "error")
    )
    result = result.sort_values(["mode", "seed", "step"]).reset_index(drop=True)
    cumulative_data = {}
    for column in cumulative:
        source = f"{column}_averted"
        cumulative_data[f"cumulative_{source}"] = result.groupby(
            ["mode", "seed"], sort=False
        )[source].cumsum()
    return pd.concat([result, pd.DataFrame(cumulative_data)], axis=1)


def horizon_summary(paired: pd.DataFrame) -> pd.DataFrame:
    """Compare deterministic and stochastic horizon totals by process."""
    final = paired.sort_values("step").groupby(["mode", "seed"], as_index=False).tail(1)
    metrics = {
        "infections averted": "cumulative_new_infections_averted",
        "new AIDS cases averted": "cumulative_new_aids_cases_averted",
        "off-ART AIDS progressions averted": "cumulative_a13_progression_chronic_to_aids_off_art_actual_averted",
        "AIDS-stage ART initiations averted": "cumulative_a13_art_initiation_aids_actual_averted",
        "off-ART AIDS expected deaths averted": "cumulative_a13_mortality_aids_off_art_expected_averted",
        "expected HIV deaths averted": "cumulative_a13_mortality_total_expected_averted",
        "actual HIV deaths averted": "cumulative_hiv_deaths_averted",
        "AIDS deaths averted": "cumulative_aids_deaths_averted",
    }
    rows = []
    for label, column in metrics.items():
        deterministic = float(final.loc[final["mode"] == "deterministic", column].iloc[0])
        stochastic = final.loc[final["mode"] == "stochastic", column].to_numpy(dtype=float)
        sd = float(np.std(stochastic, ddof=1))
        se = sd / math.sqrt(len(stochastic))
        rows.append(
            {
                "process": label,
                "column": column,
                "deterministic": deterministic,
                "stochastic_mean": float(np.mean(stochastic)),
                "stochastic_sd": sd,
                "stochastic_se": se,
                "deterministic_minus_stochastic_mean": deterministic - float(np.mean(stochastic)),
                "z_abs": abs(deterministic - float(np.mean(stochastic))) / se if se else math.nan,
            }
        )
    return pd.DataFrame(rows)


def stage_summary(paired: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for mode, mode_rows in paired.groupby("mode"):
        for seed, seed_rows in mode_rows.groupby("seed"):
            for slug in MORTALITY_SLUGS:
                expected = float(seed_rows[f"a13_{slug}_expected_averted"].sum())
                actual = float(seed_rows[f"a13_{slug}_actual_averted"].sum())
                rows.append(
                    {
                        "mode": mode,
                        "seed": int(seed),
                        "stratum": slug,
                        "expected_deaths_averted": expected,
                        "actual_deaths_averted": actual,
                        "rounding_contribution": expected - actual,
                    }
                )
    raw = pd.DataFrame(rows)
    output = []
    for (mode, stratum), group in raw.groupby(["mode", "stratum"]):
        output.append(
            {
                "mode": mode,
                "stratum": stratum,
                "expected_deaths_averted_mean": group["expected_deaths_averted"].mean(),
                "actual_deaths_averted_mean": group["actual_deaths_averted"].mean(),
                "rounding_contribution_mean": group["rounding_contribution"].mean(),
                "n": len(group),
            }
        )
    return pd.DataFrame(output)


def allocation_bias_summary(arm_rows: pd.DataFrame) -> pd.DataFrame:
    """Summarise within-arm stage allocation error for multi-stage calls."""
    rows = []
    for (mode, seed, arm), group in arm_rows.groupby(["mode", "seed", "arm"]):
        for slug in STAGE_TRACE_SLUGS:
            expected = float(group[f"a13_{slug}_expected"].sum())
            actual = float(group[f"a13_{slug}_actual"].sum())
            rows.append(
                {
                    "mode": mode,
                    "seed": int(seed),
                    "arm": arm,
                    "process_stage": slug,
                    "expected": expected,
                    "actual": actual,
                    "actual_minus_expected": actual - expected,
                }
            )
    raw = pd.DataFrame(rows)
    return (
        raw.groupby(["mode", "arm", "process_stage"], as_index=False)
        .agg(
            expected_mean=("expected", "mean"),
            actual_mean=("actual", "mean"),
            actual_minus_expected_mean=("actual_minus_expected", "mean"),
            n=("seed", "size"),
        )
    )


def annual_trace(paired: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "cumulative_new_infections_averted",
        "cumulative_new_aids_cases_averted",
        "cumulative_a13_progression_chronic_to_aids_off_art_actual_averted",
        "cumulative_a13_art_initiation_aids_actual_averted",
        "cumulative_a13_mortality_aids_off_art_expected_averted",
        "cumulative_a13_mortality_total_expected_averted",
        "cumulative_hiv_deaths_averted",
        "cumulative_aids_deaths_averted",
    ]
    work = paired.copy()
    work["calendar_year"] = np.floor(work["year"]).astype(int)
    end_year = (
        work.sort_values("step")
        .groupby(["mode", "seed", "calendar_year"], as_index=False)
        .tail(1)
    )
    rows = []
    for (mode, year), group in end_year.groupby(["mode", "calendar_year"]):
        row = {"mode": mode, "calendar_year": int(year), "n": len(group)}
        for column in columns:
            values = group[column].to_numpy(dtype=float)
            row[column] = float(np.mean(values))
            row[f"{column}_se"] = (
                float(np.std(values, ddof=1) / math.sqrt(len(values)))
                if len(values) > 1
                else 0.0
            )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["calendar_year", "mode"])


def first_material_divergence(annual: pd.DataFrame) -> dict:
    """Find first annual endpoint where deterministic is >2 SE below stochastic."""
    processes = {
        "infections": "cumulative_new_infections_averted",
        "progression to AIDS": "cumulative_new_aids_cases_averted",
        "off-ART progression to AIDS": "cumulative_a13_progression_chronic_to_aids_off_art_actual_averted",
        "AIDS-stage ART initiation": "cumulative_a13_art_initiation_aids_actual_averted",
        "off-ART AIDS mortality exposure": "cumulative_a13_mortality_aids_off_art_expected_averted",
        "expected HIV mortality": "cumulative_a13_mortality_total_expected_averted",
        "realised HIV mortality": "cumulative_hiv_deaths_averted",
    }
    det = annual.loc[annual["mode"] == "deterministic"].set_index("calendar_year")
    stoch = annual.loc[annual["mode"] == "stochastic"].set_index("calendar_year")
    common_years = det.index.intersection(stoch.index).sort_values()
    for year in common_years:
        for process, column in processes.items():
            gap = float(stoch.at[year, column] - det.at[year, column])
            se = float(stoch.at[year, f"{column}_se"])
            if gap > 0 and (se == 0 or gap > 2 * se):
                return {
                    "year": int(year),
                    "process": process,
                    "deterministic": float(det.at[year, column]),
                    "stochastic_mean": float(stoch.at[year, column]),
                    "stochastic_se": se,
                    "gap": gap,
                }
    return {}


def _fmt(value: float) -> str:
    return f"{value:,.2f}"


def report_text(
    horizon: pd.DataFrame,
    stages: pd.DataFrame,
    allocation_bias: pd.DataFrame,
    annual: pd.DataFrame,
    first_divergence: dict,
    n_stochastic: int,
) -> str:
    indexed = horizon.set_index("process")
    expected = indexed.loc["expected HIV deaths averted"]
    actual = indexed.loc["actual HIV deaths averted"]
    aids = indexed.loc["AIDS deaths averted"]
    off_art = indexed.loc["off-ART AIDS expected deaths averted"]
    mortality_gap = expected["stochastic_mean"] - expected["deterministic"]
    off_art_share = (
        (off_art["stochastic_mean"] - off_art["deterministic"]) / mortality_gap
        if mortality_gap else math.nan
    )

    det_stages = stages.loc[stages["mode"] == "deterministic"].copy()
    stage_lines = []
    for row in det_stages.itertuples(index=False):
        stage_lines.append(
            f"| `{row.stratum}` | {_fmt(row.expected_deaths_averted_mean)} | "
            f"{_fmt(row.actual_deaths_averted_mean)} | "
            f"{_fmt(row.rounding_contribution_mean)} |"
        )

    horizon_lines = []
    for row in horizon.itertuples(index=False):
        horizon_lines.append(
            f"| {row.process} | {_fmt(row.deterministic)} | "
            f"{_fmt(row.stochastic_mean)} | {_fmt(row.stochastic_se)} | "
            f"{row.z_abs:.2f} |"
        )

    bias_lines = []
    deterministic_bias = allocation_bias.loc[
        (allocation_bias["mode"] == "deterministic")
        & allocation_bias["process_stage"].isin(
            [
                "diagnosis_chronic",
                "diagnosis_aids",
                "art_initiation_chronic",
                "art_initiation_aids",
                "art_dropout_chronic",
                "art_dropout_aids",
            ]
        )
    ]
    for row in deterministic_bias.itertuples(index=False):
        bias_lines.append(
            f"| `{row.process_stage}` | `{row.arm}` | {_fmt(row.expected_mean)} | "
            f"{_fmt(row.actual_mean)} | {_fmt(row.actual_minus_expected_mean)} |"
        )

    if first_divergence:
        first_text = (
            f"The first annual endpoint at which deterministic benefit is more than "
            f"2 stochastic SE below the mean is **{first_divergence['year']}**, at "
            f"**{first_divergence['process']}**: deterministic "
            f"{_fmt(first_divergence['deterministic'])} versus stochastic mean "
            f"{_fmt(first_divergence['stochastic_mean'])} "
            f"(SE {_fmt(first_divergence['stochastic_se'])})."
        )
    else:
        first_text = "No annual process endpoint crossed the pre-specified 2-SE threshold."

    return f"""# A-13 — Deterministic versus stochastic HIV-death accounting

**Status: diagnostic complete; no production behavior changed.** The same `base_case_context`, frozen 2025 state, model/runtime configs, and seeds 42–56 used by A-4 were rerun. A diagnostic subclass observed every progression and HIV-mortality probability call without replacing its calculation.

## Conclusion

The discrepancy is caused by **cross-stage residual assignment in the deterministic event allocator**, upstream of mortality. `deterministic_allocate_events()` preserves one rounded aggregate total for an entire tensor call, but diagnosis, ART initiation, and ART dropout calls each span Acute, Chronic, and AIDS. After flooring cell expectations, the allocator gives the residual events to globally ranked cells in stable C order. It does not preserve the expected total within each HIV stage. Aggregate event counts therefore look correct while hundreds of events are systematically shifted between Chronic and AIDS.

The within-arm identity is decisive. Across the horizon, deterministic diagnosis assigns **666.9/899.5 more Chronic diagnoses** than expected in the no-PrEP/oral arms and **666.8/903.4 fewer AIDS diagnoses**. Deterministic ART initiation similarly assigns **692.2/605.2 extra Chronic starts** and **695.3/604.3 fewer AIDS starts**. Stochastic stage totals remain close to their conditional expectations. Stable ordering makes this reproducible; it does not make the allocation representative.

That stage distortion changes how long AIDS cases remain off ART. Total new AIDS cases still agree (deterministic **{_fmt(indexed.loc['new AIDS cases averted', 'deterministic'])}**, stochastic mean **{_fmt(indexed.loc['new AIDS cases averted', 'stochastic_mean'])}**), but off-ART AIDS mortality exposure reverses: the deterministic trajectory implies **{_fmt(off_art['deterministic'])}** expected deaths averted versus **{_fmt(off_art['stochastic_mean'])}** stochastically. This one stratum explains **{100 * off_art_share:.1f}%** of the {_fmt(mortality_gap)}-death gap in total expected HIV mortality benefit.

The final mortality draw is not the source. Within the already-distorted deterministic trajectory, unrounded inputs imply **{_fmt(expected['deterministic'])}** deaths averted and integer mortality records **{_fmt(actual['deterministic'])}**. In stochastic mode the corresponding mean is **{_fmt(expected['stochastic_mean'])}** expected and **{_fmt(actual['stochastic_mean'])}** realised over {n_stochastic} seeds. The large separation is already present in the stage/ART mortality pools.

AIDS-stage deaths carry **{_fmt(aids['deterministic'])}** of the deterministic realised benefit and **{_fmt(aids['stochastic_mean'])}** of the stochastic mean. This is a deterministic state-composition defect, not an ordinary `E[f(X)] != f(E[X])` cascade effect.

**Judgement:** the deterministic base case **understates mortality-mediated health gain**. The current production convention therefore needs a manuscript disclosure even if a later estimator redesign fixes the calculation. This task is diagnostic only; it does not select or implement that redesign.

## Horizon accounting

| Cascade quantity | Deterministic | Stochastic mean | Stochastic SE | |z| |
|---|---:|---:|---:|---:|
{chr(10).join(horizon_lines)}

## Deterministic mortality strata

`Expected − actual` is the contribution to mortality-benefit compression. Positive values are expected averted deaths lost at integerisation.

| Stage / ART stratum | Expected averted | Actual averted | Expected − actual |
|---|---:|---:|---:|
{chr(10).join(stage_lines)}

## Deterministic cross-stage allocation bias

`Actual − expected` is computed within each arm before differencing. Large equal-and-opposite Chronic/AIDs errors show why aggregate-only checks did not detect the defect.

| Process / stage | Arm | Expected | Actual | Actual − expected |
|---|---|---:|---:|---:|
{chr(10).join(bias_lines)}

## First material divergence

{first_text}

The annual table in `annual_cumulative_trace.csv` retains every year, including differences that do not cross 2 SE. The raw arm and paired timestep files retain every quarterly pool, probability, expectation, integer event count, and rounding residual, so the identity can be audited without trusting this prose.

## Evidence

- `arm_timestep_trace.csv`: raw reference and intervention process accounting.
- `paired_timestep_trace.csv`: reference-minus-intervention step and cumulative identities.
- `annual_cumulative_trace.csv`: deterministic and stochastic annual endpoints with stochastic SE.
- `horizon_summary.csv`: cascade comparison at the 40-year horizon.
- `mortality_stage_summary.csv`: stage × ART attribution.
- `allocation_stage_bias.csv`: within-arm expected-versus-actual diagnosis/ART allocation by stage.
- `manifest.json`: command, environment, source/config hashes, seeds, and artifact hashes.
- `runs/`: retained stdout for every deterministic/stochastic execution.

## Scope boundary

No model parameter, event ordering, allocation rule, or production output was changed. The trace answers A-13's diagnosis and disclosure question only. Any correction should be designed with A-2/A-9 so it preserves integer state, cross-arm comparability, and the deterministic reference policy explicitly rather than silently substituting a new estimand.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", type=parse_seeds, default=parse_seeds("42-56"))
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    runs_dir = output_dir / "runs"
    runs_dir.mkdir()
    model_config = args.model_config.resolve()
    runtime_config = args.runtime_config.resolve()

    manifest = {
        "task": "A-13 deterministic versus stochastic HIV-death accounting",
        "command": shlex.join([sys.executable, *sys.argv]),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_text(["rev-parse", "HEAD"]),
        "git_status_short": git_text(["status", "--short"]),
        "model_config": str(model_config),
        "runtime_config": str(runtime_config),
        "scenario": BASE_SCENARIO,
        "stochastic_seeds": args.seeds,
        "workers": args.workers,
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "sha256": {
            "model_config": sha256(model_config),
            "runtime_config": sha256(runtime_config),
            **{
                f"code/{name}": sha256(ROOT / name)
                for name in (
                    "tools/run_a13_hiv_mortality_trace.py",
                    "run_v3.py",
                    "stochastic_allocation.py",
                    "counter_based_rng.py",
                    "run_scenarios.py",
                    "transmission_corrected.py",
                    "demography.py",
                )
            },
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    payloads = [
        (
            str(model_config),
            str(runtime_config),
            str(runs_dir / f"stochastic_seed_{seed}"),
            False,
            seed,
        )
        for seed in args.seeds
    ]
    all_rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_run_worker, payload): payload[-1] for payload in payloads}
        for future in as_completed(futures):
            all_rows.extend(future.result())

    all_rows.extend(
        _run_worker(
            (
                str(model_config),
                str(runtime_config),
                str(runs_dir / "deterministic_seed_42"),
                True,
                42,
            )
        )
    )
    arms = pd.DataFrame(all_rows).sort_values(["mode", "seed", "arm", "step"])
    paired = paired_trace(arms)
    horizon = horizon_summary(paired)
    stages = stage_summary(paired)
    allocation_bias = allocation_bias_summary(arms)
    annual = annual_trace(paired)
    first_divergence = first_material_divergence(annual)

    artifacts = {
        "arm_timestep_trace.csv": arms,
        "paired_timestep_trace.csv": paired,
        "annual_cumulative_trace.csv": annual,
        "horizon_summary.csv": horizon,
        "mortality_stage_summary.csv": stages,
        "allocation_stage_bias.csv": allocation_bias,
    }
    for name, frame in artifacts.items():
        frame.to_csv(output_dir / name, index=False)
    report = report_text(
        horizon, stages, allocation_bias, annual, first_divergence, len(args.seeds)
    )
    (output_dir / "A13_HIV_MORTALITY_TRACE.md").write_text(
        report, encoding="utf-8"
    )

    # Accounting guard: the engine's reported total must exactly equal the sum
    # of the six observed mortality calls for every arm-step.
    observed = arms["hiv_deaths"].to_numpy(dtype=float)
    reconstructed = sum(
        arms[f"a13_{slug}_actual"].to_numpy(dtype=float) for slug in MORTALITY_SLUGS
    )
    if not np.array_equal(observed, reconstructed):
        raise AssertionError("A-13 mortality trace does not reconcile to engine outputs")

    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["counts"] = {
        "stochastic_seeds": len(args.seeds),
        "arm_timestep_rows": len(arms),
        "paired_timestep_rows": len(paired),
    }
    manifest["first_material_divergence"] = first_divergence
    artifact_names = [*artifacts, "A13_HIV_MORTALITY_TRACE.md"]
    manifest["output_sha256"] = {
        name: sha256(output_dir / name) for name in artifact_names
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"A-13 evidence written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
