#!/usr/bin/env python3
"""Run A-20's process-by-process deterministic-level decomposition."""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import json
import platform
import shlex
import subprocess
import sys
import time
from collections import Counter
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_extractor import extract_annual_outputs  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402
from tools.a17_residual_placement import METRICS, compare_annual_levels  # noqa: E402
from tools.a20_process_decomposition import (  # noqa: E402
    PROCESS_FAMILIES,
    SWEEP_ARMS,
    ProcessModeSwitch,
)
from tools.run_b8_deterministic_target_level_comparison import (  # noqa: E402
    target_years_from_register,
)

START_YEAR = 1975
END_YEAR = 2025
DEFAULT_SEEDS = tuple(range(7001, 7051))
BASELINE_MEDIAN_ABS_LOG_RATIO = 0.18838
MATERIAL_THRESHOLD = 0.09419
MEDIAN_SE = 0.020609
NON_NULL_THRESHOLD = 3.0 * MEDIAN_SE

MODULES = (
    "run_v3.py",
    "demography.py",
    "migration.py",
    "transmission_corrected.py",
    "adverse_events.py",
)

FUNCTION_FAMILY = {
    ("run_v3.py", "_draw_events"): "unscoped_event_draw",
    ("run_v3.py", "_process_rng"): "shared_crn_routing",
    ("run_v3.py", "_draw_events_for"): "shared_named_event_dispatch",
    ("run_v3.py", "_apply_fractional_aging_adults_legacy"): "ageing_sexual_debut",
    ("demography.py", "apply_mortality"): "background_mortality",
    ("demography.py", "process_continuous_debut"): "ageing_sexual_debut",
    ("demography.py", "apply_fractional_aging_legacy"): "ageing_sexual_debut",
    ("demography.py", "calculate_births"): "births",
    ("demography.py", "add_births_to_childhood"): "births",
    ("demography.py", "process_sexual_debut"): "ageing_sexual_debut",
    ("demography.py", "_distribute_to_activities"): "ageing_sexual_debut",
    ("demography.py", "_calculate_constant_mortality"): "background_mortality",
    ("demography.py", "_calculate_life_table_mortality"): "background_mortality",
    ("migration.py", "_distribute_int"): "migration",
    ("transmission_corrected.py", "_calc_msm_infections"): "transmission",
    ("transmission_corrected.py", "calculate_transmission"): "transmission",
    ("adverse_events.py", "_item_state_machine_ongoing"): "adverse_events",
}

RULES = {
    ("run_v3.py", "_draw_events"): "deterministic_allocate_events vs binomial",
    ("run_v3.py", "_process_rng"): "shared RNG vs counter-based CRN factory",
    ("run_v3.py", "_draw_events_for"): "deterministic_allocate_events vs binomial",
    ("run_v3.py", "_apply_fractional_aging_adults_legacy"): "np.round(staying) vs binomial(moving)",
    ("demography.py", "apply_mortality"): "round(pop*rate) vs binomial",
    ("demography.py", "process_continuous_debut"): "int(n*p) vs binomial",
    ("demography.py", "apply_fractional_aging_legacy"): "np.round(staying) vs binomial(moving)",
    ("demography.py", "calculate_births"): "np.round(expected) vs poisson",
    ("demography.py", "add_births_to_childhood"): "int(n*fraction_male) vs binomial",
    ("demography.py", "process_sexual_debut"): "int(n*p_msm) vs binomial",
    ("demography.py", "_distribute_to_activities"): "largest remainder vs multinomial",
    ("demography.py", "_calculate_constant_mortality"): "deterministic_allocate_events vs binomial",
    ("demography.py", "_calculate_life_table_mortality"): "deterministic_allocate_events vs binomial",
    ("migration.py", "_distribute_int"): "largest remainder vs multinomial",
    ("transmission_corrected.py", "_calc_msm_infections"): "int(expected) vs binomial",
    ("transmission_corrected.py", "calculate_transmission"): "fractional expectation plus aggregate allocation vs stratified binomials",
    ("adverse_events.py", "_item_state_machine_ongoing"): "int(expected) vs binomial",
}


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


def parse_seeds(text: str) -> list[int]:
    seeds = []
    for token in text.split(","):
        token = token.strip()
        if "-" in token:
            first, last = map(int, token.split("-", 1))
            seeds.extend(range(first, last + 1))
        elif token:
            seeds.append(int(token))
    if len(seeds) != len(set(seeds)):
        raise argparse.ArgumentTypeError("seeds must be distinct")
    return seeds


def configured(base: dict, *, deterministic: bool, seed: int) -> dict:
    config = json.loads(json.dumps(base))
    config["run_control"]["deterministic_mode"] = {"value": deterministic}
    config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": deterministic
    }
    config["run_control"]["seed"] = {"value": int(seed)}
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    return config


class BranchLineCounter(AbstractContextManager):
    def __init__(self, sites):
        self.sites = {(str((ROOT / m).resolve()), int(line)) for m, line in sites}
        self.counts = Counter()

    def _trace(self, frame, event, arg):
        if event == "line":
            key = (str(Path(frame.f_code.co_filename).resolve()), frame.f_lineno)
            if key in self.sites:
                self.counts[key] += 1
        return self._trace

    def __enter__(self):
        sys.settrace(self._trace)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        sys.settrace(None)
        return False


def inventory_sites() -> list[dict]:
    rows = []
    for module in MODULES:
        path = ROOT / module
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                functions.append(node.name)
                self.generic_visit(node)
                functions.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_If(self, node):
                expression = ast.unparse(node.test)
                if "deterministic" in expression:
                    function = functions[-1] if functions else "<module>"
                    key = (module, function)
                    if key not in FUNCTION_FAMILY or key not in RULES:
                        raise AssertionError(
                            f"unmapped deterministic branch: {module}:{node.lineno} {function}"
                        )
                    rows.append(
                        {
                            "module": module,
                            "line": int(node.lineno),
                            "function": function,
                            "flag_source": (
                                "self.deterministic"
                                if "self.deterministic" in expression
                                else "deterministic parameter"
                            ),
                            "process_family": FUNCTION_FAMILY[key],
                            "deterministic_rule": RULES[key],
                            "branch_expression": expression,
                        }
                    )
                self.generic_visit(node)

        Visitor().visit(tree)
    if len({(r["module"], r["line"]) for r in rows}) != len(rows):
        raise AssertionError("a switch-surface branch site was mapped more than once")
    return sorted(rows, key=lambda r: (MODULES.index(r["module"]), r["line"]))


def run_annual(base, *, deterministic, seed, forced, run_number, log):
    config = configured(base, deterministic=deterministic, seed=seed)
    output = io.StringIO()
    mode = ProcessModeSwitch(forced) if forced is not None else contextlib.nullcontext()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        with mode as switch:
            result = SimulationEngineV3(config, seed=seed).run(run_number=run_number)
    label = "production" if forced is None else "+".join(sorted(forced)) or "none_forced"
    log.write(f"===== {label} deterministic={deterministic} seed={seed} =====\n")
    log.write(output.getvalue())
    annual = extract_annual_outputs(result, start_year=START_YEAR, end_year=END_YEAR)
    coverage = Counter() if forced is None else switch.coverage
    rng_requests = Counter() if forced is None else switch.rng_requests
    surface_calls = Counter() if forced is None else switch.surface_calls
    return annual, coverage, rng_requests, surface_calls


def annual_rows(annual, *, arm, seed):
    expected_years = set(range(START_YEAR, END_YEAR + 1))
    rows = []
    for metric in METRICS:
        if metric not in annual or set(annual[metric]) != expected_years:
            raise AssertionError(f"incomplete annual extraction for {metric}")
        rows.extend(
            {
                "arm": arm,
                "seed": int(seed),
                "metric": metric,
                "year": year,
                "value": float(annual[metric][year]),
            }
            for year in sorted(expected_years)
        )
    return rows


def annual_digest(annual) -> str:
    payload = json.dumps(
        {
            metric: {str(y): float(v) for y, v in annual[metric].items()}
            for metric in METRICS
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def score(candidate, reference, target_years, *, arm):
    comparison = compare_annual_levels(candidate, reference)
    comparison["arm"] = arm
    scored = pd.concat(
        [
            comparison[
                (comparison["metric"] == metric)
                & comparison["year"].isin(years)
            ]
            for metric, years in target_years.items()
        ]
    ).sort_values(["metric", "year"])
    return scored.reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--target-register", type=Path, required=True)
    parser.add_argument("--reference-replicates", type=Path, required=True)
    parser.add_argument("--a5a-replicates", type=Path, required=True)
    parser.add_argument("--b8-deterministic", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", type=parse_seeds, default=list(DEFAULT_SEEDS))
    args = parser.parse_args()

    if list(args.seeds) != list(DEFAULT_SEEDS):
        raise ValueError("A-20 seed set is fixed at 7001-7050")

    config_path = args.model_config.resolve()
    register_path = args.target_register.resolve()
    reference_path = args.reference_replicates.resolve()
    a5a_path = args.a5a_replicates.resolve()
    b8_path = args.b8_deterministic.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    provenance_dir = output_dir / "provenance"
    raw_dir.mkdir()
    provenance_dir.mkdir()

    status = git_text("status", "--short")
    diff = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    (provenance_dir / "git_status_short.txt").write_text(
        status + ("\n" if status else ""), encoding="utf-8"
    )
    (provenance_dir / "git_diff.patch").write_bytes(diff)

    # Written before any model run: this is a prior, not a finding from A-20.
    precheck = {
        "source": str(
            ROOT
            / "outputs/hta_results/a13_hiv_mortality_trace_20260910_230000/A13_HIV_MORTALITY_TRACE.md"
        ),
        "direction": (
            "A-13's deterministic residual placement leaves more AIDS cases "
            "undiagnosed and off ART, increasing mortality and tending to deflate "
            "PLHIV; B-8 is inflation. The direction is therefore wrong."
        ),
        "order_of_magnitude": (
            "A-13 measures a 61.55-death expected-mortality-benefit gap over a "
            "40-year HTA horizon. Against a PLHIV stock gap measured in thousands, "
            "this is sub-1% order, not +20%."
        ),
        "decision": "wrong direction and far too small; proceed to the full sweep",
    }
    (raw_dir / "step0_a13_precheck.json").write_text(
        json.dumps(precheck, indent=2) + "\n", encoding="utf-8"
    )

    sites = inventory_sites()
    if len(sites) != 31:
        raise AssertionError(f"expected 31 current branch sites, found {len(sites)}")

    register = json.loads(register_path.read_text(encoding="utf-8"))
    target_years = target_years_from_register(register)
    if sum(map(len, target_years.values())) != 222:
        raise AssertionError("A-20 target register no longer yields 222 scored years")

    reference_all = pd.read_csv(reference_path)
    reference = reference_all[reference_all["arm"] == "stochastic_reference"].copy()
    if sorted(reference["seed"].unique()) != list(DEFAULT_SEEDS):
        raise AssertionError("reference ensemble is not seeds 7001-7050")
    a5a = pd.read_csv(a5a_path).drop(columns=["scenario"], errors="ignore")
    overlap = reference.merge(
        a5a,
        on=["seed", "metric", "year"],
        suffixes=("_a18", "_a5a"),
        how="inner",
    )
    reference_check = {
        "n_overlap_rows": int(len(overlap)),
        "max_absolute_difference": float(
            (overlap["value_a18"] - overlap["value_a5a"]).abs().max()
        ),
    }
    if reference_check["n_overlap_rows"] != 11100 or reference_check["max_absolute_difference"] != 0.0:
        raise AssertionError("A-18 and post-CRN A-5a stochastic references differ")
    reference.to_csv(raw_dir / "stochastic_reference_1975_2025.csv", index=False)
    (raw_dir / "reference_equivalence.json").write_text(
        json.dumps(reference_check, indent=2) + "\n", encoding="utf-8"
    )

    command = shlex.join([sys.executable, *sys.argv])
    manifest = {
        "task": "A-20 process-by-process decomposition",
        "command": command,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path),
        "target_register": str(register_path),
        "seeds": list(args.seeds),
        "n_replicates_per_arm": len(args.seeds),
        "window": [START_YEAR, END_YEAR],
        "arm_order": list(SWEEP_ARMS),
        "pre_specification": {
            "baseline_median_absolute_log_ratio": BASELINE_MEDIAN_ABS_LOG_RATIO,
            "material_threshold": MATERIAL_THRESHOLD,
            "median_standard_error": MEDIAN_SE,
            "material_threshold_in_median_se": MATERIAL_THRESHOLD / MEDIAN_SE,
            "non_null_displacement_threshold": NON_NULL_THRESHOLD,
            "non_null_threshold_in_median_se": NON_NULL_THRESHOLD / MEDIAN_SE,
        },
        "git_commit": git_text("rev-parse", "HEAD"),
        "git_dirty": bool(status),
        "python": sys.version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "platform": platform.platform(),
        "input_sha256": {
            str(p.relative_to(ROOT)): sha256(p)
            for p in [
                config_path,
                register_path,
                reference_path,
                a5a_path,
                b8_path,
                *(ROOT / module for module in MODULES),
                ROOT / "tools/a20_process_decomposition.py",
                Path(__file__).resolve(),
            ]
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    base = json.loads(config_path.read_text(encoding="utf-8"))
    timings = []
    coverage_rows = []
    all_rows = []
    started = time.time()

    def timed(label, seed, **kwargs):
        t0 = time.time()
        annual, coverage, rng_requests, surface_calls = run_annual(
            base, seed=seed, log=log, **kwargs
        )
        timings.append(
            {"arm": label, "seed": seed, "elapsed_seconds": time.time() - t0}
        )
        return annual, coverage, rng_requests, surface_calls

    with (output_dir / "runner_stdout.log").open("w", encoding="utf-8") as log:
        seed = args.seeds[0]
        print("A-20 Step 0: full-window equivalence controls", flush=True)
        prod_det, _, _, _ = timed(
            "production_deterministic", seed,
            deterministic=True, forced=None, run_number=1,
        )
        no_forced, no_cov, no_rng, no_surface = timed(
            "no_family_forced", seed,
            deterministic=True, forced=frozenset(), run_number=2,
        )
        prod_sto, _, _, _ = timed(
            "production_stochastic", seed,
            deterministic=False, forced=None, run_number=3,
        )
        all_forced, all_cov, all_rng, all_surface = timed(
            "all_families_forced", seed,
            deterministic=True, forced=PROCESS_FAMILIES, run_number=4,
        )
        equivalence = {
            "production_deterministic_sha256": annual_digest(prod_det),
            "no_family_forced_sha256": annual_digest(no_forced),
            "production_stochastic_seed_7001_sha256": annual_digest(prod_sto),
            "all_families_forced_seed_7001_sha256": annual_digest(all_forced),
        }
        equivalence["deterministic_corner_bit_identical"] = (
            equivalence["production_deterministic_sha256"]
            == equivalence["no_family_forced_sha256"]
        )
        equivalence["stochastic_corner_bit_identical"] = (
            equivalence["production_stochastic_seed_7001_sha256"]
            == equivalence["all_families_forced_seed_7001_sha256"]
        )
        (raw_dir / "step0_equivalence_control.json").write_text(
            json.dumps(equivalence, indent=2) + "\n", encoding="utf-8"
        )
        if not all(
            equivalence[key]
            for key in (
                "deterministic_corner_bit_identical",
                "stochastic_corner_bit_identical",
            )
        ):
            raise AssertionError(
                "A-20 equivalence corner failed; no process arm may be run"
            )

        b8 = pd.read_csv(b8_path)
        fresh_det = pd.DataFrame(
            annual_rows(prod_det, arm="deterministic_baseline", seed=seed)
        )
        b8_cmp = fresh_det.merge(
            b8,
            on=["metric", "year"],
            suffixes=("_fresh", "_b8"),
        )
        b8_scored = pd.concat(
            [
                b8_cmp[
                    (b8_cmp["metric"] == metric)
                    & b8_cmp["year"].isin(years)
                ]
                for metric, years in target_years.items()
            ]
        )
        max_b8_diff = float(
            (b8_scored["value_fresh"] - b8_scored["value_b8"]).abs().max()
        )
        b8_control = {
            "n_scored_target_years": int(len(b8_scored)),
            "max_absolute_difference": max_b8_diff,
        }
        (raw_dir / "step0_b8_reproduction.json").write_text(
            json.dumps(b8_control, indent=2) + "\n", encoding="utf-8"
        )
        if len(b8_scored) != 222 or max_b8_diff != 0.0:
            raise AssertionError("fresh deterministic baseline does not reproduce B-8")

        for row in sites:
            if row["function"] == "apply_mortality":
                key = f"{row['module']}:{row['function']}:{row['line']}"
            else:
                key = f"{row['module']}:{row['function']}"
            row["exercised_calls_per_run"] = int(
                max(no_surface[key], all_surface[key])
            )
            row["config_status"] = (
                "live" if row["exercised_calls_per_run"] > 0 else "dormant"
            )
        pd.DataFrame(sites).to_csv(raw_dir / "switch_surface_inventory.csv", index=False)

        print("A-20 Step 1: diagnosis and ART first, then remaining arms", flush=True)
        run_number = 100
        for arm, forced in SWEEP_ARMS.items():
            arm_rows = []
            for index, arm_seed in enumerate(args.seeds, start=1):
                run_number += 1
                annual, coverage, rng_requests, _surface_calls = timed(
                    arm,
                    arm_seed,
                    deterministic=True,
                    forced=forced,
                    run_number=run_number,
                )
                arm_rows.extend(annual_rows(annual, arm=arm, seed=arm_seed))
                for family in PROCESS_FAMILIES:
                    coverage_rows.append(
                        {
                            "arm": arm,
                            "seed": arm_seed,
                            "process_family": family,
                            "forced_stochastic": family in forced,
                            "process_calls": int(coverage.get(family, 0)),
                            "rng_requests": int(rng_requests.get(family, 0)),
                        }
                    )
                print(
                    f"A-20 {arm} {index}/{len(args.seeds)} seed={arm_seed}",
                    flush=True,
                )
            all_rows.extend(arm_rows)
            pd.DataFrame(all_rows).to_csv(
                raw_dir / "step1_arm_replicates_1975_2025.csv", index=False
            )
            pd.DataFrame(coverage_rows).to_csv(
                raw_dir / "coverage_audit_per_run.csv", index=False
            )

    det_frame = pd.DataFrame(
        annual_rows(prod_det, arm="deterministic_baseline", seed=args.seeds[0])
    )
    det_frame.to_csv(raw_dir / "deterministic_baseline_1975_2025.csv", index=False)
    arm_replicates = pd.DataFrame(all_rows)
    arm_means = (
        arm_replicates.groupby(["arm", "metric", "year"], as_index=False)["value"]
        .mean()
    )

    comparisons = []
    summaries = []
    baseline_scored = score(
        det_frame[["metric", "year", "value"]], reference, target_years,
        arm="deterministic_baseline",
    )
    baseline_observed = float(
        baseline_scored["log_ratio_det_over_stoch_mean"].abs().median()
    )
    if not np.isclose(baseline_observed, 0.1883772, atol=5e-7):
        raise AssertionError(f"unexpected A-20 baseline {baseline_observed}")

    candidates = {"deterministic_baseline": det_frame}
    for arm in SWEEP_ARMS:
        candidates[arm] = arm_means[arm_means["arm"] == arm]
    # Recompute (do not rerun) the A-18 transmission arm for the same ranking table.
    candidates["transmission_a18"] = reference_all[
        reference_all["arm"] == "hybrid_transmission"
    ].groupby(["metric", "year"], as_index=False)["value"].mean()

    det_values = det_frame[["metric", "year", "value"]].rename(
        columns={"value": "deterministic_value"}
    )
    for arm, candidate in candidates.items():
        candidate = candidate[["metric", "year", "value"]]
        scored = score(candidate, reference, target_years, arm=arm)
        comparisons.append(scored)
        median_abs = float(scored["log_ratio_det_over_stoch_mean"].abs().median())
        displacement = baseline_observed - median_abs
        versus_det = scored[["metric", "year", "deterministic"]].rename(
            columns={"deterministic": "candidate_value"}
        ).merge(det_values, on=["metric", "year"])
        valid = (versus_det["candidate_value"] > 0) & (
            versus_det["deterministic_value"] > 0
        )
        log_move = np.log(
            versus_det.loc[valid, "candidate_value"]
            / versus_det.loc[valid, "deterministic_value"]
        )
        abs_pct_move = (
            100.0
            * (
                versus_det.loc[valid, "candidate_value"]
                / versus_det.loc[valid, "deterministic_value"]
                - 1.0
            ).abs()
        )
        median_abs_pct_move = float(abs_pct_move.median())
        if arm == "deterministic_baseline":
            allocation_class = "not applicable"
        elif median_abs_pct_move <= 1.0:
            allocation_class = "<=1%; not separable from allocation removal"
        elif median_abs_pct_move >= 10.0:
            allocation_class = ">=10%; too large for measured allocation removal alone"
        else:
            allocation_class = "between 1% and 10%; allocation confound unresolved"
        summaries.append(
            {
                "arm": arm,
                "n_replicates": 1 if arm == "deterministic_baseline" else 50,
                "reference": "post-CRN stochastic mean, n=50 seeds 7001-7050",
                "median_abs_log_ratio_vs_stochastic": median_abs,
                "displacement_from_baseline": displacement,
                "displacement_in_median_se": displacement / MEDIAN_SE,
                "material": bool(median_abs < MATERIAL_THRESHOLD),
                "non_null": bool(displacement > NON_NULL_THRESHOLD),
                "null_band_result": (
                    "non-null" if displacement > NON_NULL_THRESHOLD
                    else "indistinguishable from zero"
                ),
                "median_pct_difference_vs_stochastic": float(
                    scored["pct_difference"].median()
                ),
                "median_log_move_vs_deterministic": float(log_move.median()),
                "median_abs_pct_move_vs_deterministic": median_abs_pct_move,
                "allocation_removal_separability": allocation_class,
                "n_target_years_above_stochastic": int((scored["difference"] > 0).sum()),
                "n_target_years_inside_stochastic_range": int(
                    scored["det_inside_stochastic_range"].sum()
                ),
            }
        )

    comparison_frame = pd.concat(comparisons, ignore_index=True)
    comparison_frame.to_csv(raw_dir / "step1_scored_target_years.csv", index=False)
    summary_frame = pd.DataFrame(summaries).sort_values(
        "displacement_from_baseline", ascending=False
    )
    summary_frame["rank_by_displacement"] = np.arange(1, len(summary_frame) + 1)
    summary_frame.to_csv(raw_dir / "step1_arm_summary.csv", index=False)

    # Per-year displacement is available for every arm whether or not Step 2 fires.
    baseline_yearly = baseline_scored[
        ["metric", "year", "log_ratio_det_over_stoch_mean"]
    ].rename(columns={"log_ratio_det_over_stoch_mean": "baseline_log_ratio"})
    yearly = comparison_frame.merge(baseline_yearly, on=["metric", "year"])
    yearly["arm_log_ratio"] = yearly["log_ratio_det_over_stoch_mean"]
    yearly["absolute_log_ratio_displacement"] = (
        yearly["baseline_log_ratio"].abs() - yearly["arm_log_ratio"].abs()
    )
    yearly.to_csv(raw_dir / "step1_yearly_displacement.csv", index=False)

    means_wide = arm_means.pivot_table(
        index=["metric", "year"], columns="arm", values="value"
    ).reset_index()
    det_wide = det_values.rename(columns={"deterministic_value": "det"})
    interaction = means_wide.merge(det_wide, on=["metric", "year"])
    for arm in ("diagnosis", "art", "diagnosis_art"):
        interaction[f"{arm}_log_move"] = np.where(
            (interaction[arm] > 0) & (interaction["det"] > 0),
            np.log(interaction[arm] / interaction["det"]),
            np.nan,
        )
    interaction["interaction_log"] = (
        interaction["diagnosis_art_log_move"]
        - interaction["diagnosis_log_move"]
        - interaction["art_log_move"]
    )
    interaction.to_csv(raw_dir / "diagnosis_art_interaction_by_year.csv", index=False)
    interaction_summary = {
        "median_interaction_log_all_metric_years": float(
            interaction["interaction_log"].median()
        ),
        "median_absolute_interaction_log_all_metric_years": float(
            interaction["interaction_log"].abs().median()
        ),
        "max_absolute_interaction_log_all_metric_years": float(
            interaction["interaction_log"].abs().max()
        ),
    }
    (raw_dir / "diagnosis_art_interaction_summary.json").write_text(
        json.dumps(interaction_summary, indent=2) + "\n", encoding="utf-8"
    )

    coverage = pd.DataFrame(coverage_rows)
    coverage_summary = (
        coverage.groupby(["arm", "process_family", "forced_stochastic"], as_index=False)
        .agg(
            min_calls_per_run=("process_calls", "min"),
            median_calls_per_run=("process_calls", "median"),
            max_calls_per_run=("process_calls", "max"),
            min_rng_requests_per_run=("rng_requests", "min"),
            max_rng_requests_per_run=("rng_requests", "max"),
        )
    )
    coverage_summary["status"] = np.where(
        coverage_summary["max_calls_per_run"] > 0, "live", "dormant"
    )
    coverage_summary["note"] = np.where(
        coverage_summary["process_family"] == "adverse_events",
        "HTA-only; economics is not attached in calibration runs",
        np.where(
            coverage_summary["process_family"] == "unscoped_event_draw",
            "no direct unscoped call on this config",
            "",
        ),
    )
    coverage_summary.to_csv(raw_dir / "coverage_audit_summary.csv", index=False)

    pd.DataFrame(timings).to_csv(raw_dir / "run_timings.csv", index=False)
    findings = {
        "baseline_observed": baseline_observed,
        "material_threshold": MATERIAL_THRESHOLD,
        "non_null_threshold": NON_NULL_THRESHOLD,
        "n_material_process_arms": int(
            summary_frame[
                summary_frame["arm"].isin(list(SWEEP_ARMS))
                & summary_frame["material"]
            ].shape[0]
        ),
        "n_non_null_process_arms": int(
            summary_frame[
                summary_frame["arm"].isin(list(SWEEP_ARMS))
                & summary_frame["non_null"]
            ].shape[0]
        ),
        "interaction": interaction_summary,
        "elapsed_seconds": time.time() - started,
    }
    (raw_dir / "findings.json").write_text(
        json.dumps(findings, indent=2) + "\n", encoding="utf-8"
    )
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["elapsed_seconds"] = findings["elapsed_seconds"]
    manifest["status"] = "step1_complete"
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    hashes = {
        str(path.relative_to(output_dir)): sha256(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.txt"}
    }
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {path}\n" for path, digest in hashes.items()),
        encoding="utf-8",
    )
    print(json.dumps(findings, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
