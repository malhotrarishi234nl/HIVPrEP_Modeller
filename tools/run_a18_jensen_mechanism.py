#!/usr/bin/env python3
"""A-18 Step 1: the Jensen isolation test, plus the direct arithmetic check.

The A-18 brief makes Steps 2 and 3 (except remedy B) conditional on Step 1.
This runner therefore does four things and stops:

  0. Equivalence controls.  The parameterised transmission copy in
     ``tools/a18_jensen_mechanism.py`` is run in both of its pure corners over
     the full 1975-2025 window and asserted bit-identical to production
     deterministic and production stochastic respectively.  A hybrid built on
     an unverified copy would be worthless.

  1. The reference trajectories.  One production deterministic run and an
     n=50 production stochastic ensemble, both on current code.

  2. The two hybrids.  ``hybrid_contacts`` is the brief's literal isolation
     test (contact counts drawn, everything else deterministic).
     ``hybrid_transmission`` is a deliberately more generous variant: the
     whole production stochastic transmission path, every other process
     deterministic.  If the mechanism is anywhere in transmission, the second
     one catches it even if the first does not.

  3. The direct ``f(E[n]) - E[f(n)]`` arithmetic, evaluated on the model's own
     traced contact counts and transmission probabilities.

Nothing here changes production behaviour.  The diagnostic bindings live
inside a context manager and are restored on exit.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
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

from calib_extractor import extract_annual_outputs  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402
from tools.a17_residual_placement import METRICS, compare_annual_levels  # noqa: E402
from tools.a18_jensen_mechanism import (  # noqa: E402
    HybridTransmission,
    TransmissionTrace,
    hypothesised_form_bias,
    monte_carlo_model_form_bias,
    per_step_bias_summary,
)
from tools.run_b8_deterministic_target_level_comparison import (  # noqa: E402
    target_years_from_register,
)

START_YEAR = 1975
END_YEAR = 2025
DEFAULT_SEEDS = tuple(range(7001, 7051))

#: Pre-specified, from the A-18 brief section 3.  Written to the manifest before
#: any model is run and not touched afterwards.
BASELINE_MEDIAN_ABS_LOG_RATIO = 0.18838
SUCCESS_THRESHOLD = 0.09419
MEDIAN_SE_OF_YEARLY_LOG_RATIO = 0.02058

#: Years whose per-stratum transmission trace is retained in full.
DEFAULT_TRACE_YEARS = (1980.0, 1985.0, 1990.0, 2000.0)


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
    seeds: list[int] = []
    for token in text.split(","):
        token = token.strip()
        if "-" in token:
            start, end = (int(value) for value in token.split("-", 1))
            seeds.extend(range(start, end + 1))
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


def run_annual(
    base: dict,
    *,
    deterministic: bool,
    seed: int,
    run_number: int,
    log,
    variant: str | None = None,
    trace: TransmissionTrace | None = None,
) -> dict:
    config = configured(base, deterministic=deterministic, seed=seed)
    buffer = io.StringIO()
    context = (
        HybridTransmission(variant, trace=trace)
        if variant is not None
        else contextlib.nullcontext()
    )
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        with context:
            result = SimulationEngineV3(config, seed=seed).run(run_number=run_number)
    label = variant or ("deterministic" if deterministic else "stochastic")
    log.write(f"===== {label} seed {seed} =====\n{buffer.getvalue()}")
    return extract_annual_outputs(result, start_year=START_YEAR, end_year=END_YEAR)


def annual_rows(annual: dict, *, seed: int, arm: str) -> list[dict]:
    rows: list[dict] = []
    expected_years = set(range(START_YEAR, END_YEAR + 1))
    for metric in METRICS:
        if metric not in annual or set(annual[metric]) != expected_years:
            raise AssertionError(f"incomplete A-18 annual extraction for {metric}")
        for year in range(START_YEAR, END_YEAR + 1):
            rows.append(
                {
                    "arm": arm,
                    "seed": int(seed),
                    "metric": metric,
                    "year": year,
                    "value": float(annual[metric][year]),
                }
            )
    return rows


def annual_digest(annual: dict) -> str:
    payload = json.dumps(
        {metric: {str(y): float(v) for y, v in annual[metric].items()} for metric in METRICS},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def score_candidate(
    candidate: pd.DataFrame,
    reference: pd.DataFrame,
    target_years: dict[str, list[int]],
    *,
    label: str,
) -> tuple[pd.DataFrame, dict]:
    """Median |log(candidate / stochastic mean)| over the scored target-years."""
    comparison = compare_annual_levels(candidate, reference)
    comparison["arm"] = label
    keep = pd.concat(
        [
            comparison[(comparison["metric"] == metric) & (comparison["year"].isin(years))]
            for metric, years in target_years.items()
        ]
    ).sort_values(["metric", "year"]).reset_index(drop=True)
    abs_log = keep["log_ratio_det_over_stoch_mean"].abs()
    summary = {
        "arm": label,
        "n_scored_target_years": int(len(keep)),
        "n_finite_log_ratio": int(abs_log.notna().sum()),
        "median_abs_log_ratio": float(abs_log.median()),
        "mean_abs_log_ratio": float(abs_log.mean()),
        "max_abs_log_ratio": float(abs_log.max()),
        "n_years_candidate_above_reference": int(
            (keep["difference"] > 0).sum()
        ),
        "n_years_candidate_inside_replicate_range": int(
            keep["det_inside_stochastic_range"].sum()
        ),
        "median_pct_difference": float(keep["pct_difference"].median()),
        "meets_pre_specified_criterion": bool(
            float(abs_log.median()) < SUCCESS_THRESHOLD
        ),
    }
    return keep, summary


def ensemble_mean_frame(rows: list[dict], arm: str) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    mean = (
        frame.groupby(["metric", "year"])["value"].mean().reset_index()
    )
    mean["arm"] = arm
    return mean


def write_hashes(output_dir: Path) -> dict[str, str]:
    paths = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.txt"}
    )
    return {str(path.relative_to(output_dir)): sha256(path) for path in paths}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--target-register", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", type=parse_seeds, default=list(DEFAULT_SEEDS))
    parser.add_argument(
        "--trace-years", type=float, nargs="*", default=list(DEFAULT_TRACE_YEARS)
    )
    args = parser.parse_args()

    config_path = args.model_config.resolve()
    register_path = args.target_register.resolve()
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

    register = json.loads(register_path.read_text(encoding="utf-8"))
    target_years = target_years_from_register(register)
    n_scored = sum(len(years) for years in target_years.values())

    command = shlex.join([sys.executable, *sys.argv])
    manifest = {
        "task": "A-18 the Jensen hypothesis",
        "stage": "Step 1 isolation test + direct arithmetic check",
        "command": command,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_config": str(config_path),
        "target_register": str(register_path),
        "seeds": list(args.seeds),
        "n_replicates": len(args.seeds),
        "extraction_window": [START_YEAR, END_YEAR],
        "metrics": list(METRICS),
        "n_scored_target_years": n_scored,
        "trace_years": list(args.trace_years),
        "pre_specification": {
            "success_criterion": (
                "The median absolute log ratio of the candidate against the n=50 "
                "production stochastic mean, across the scored target-years, falls "
                "below half the deterministic baseline."
            ),
            "baseline_median_abs_log_ratio": BASELINE_MEDIAN_ABS_LOG_RATIO,
            "success_threshold": SUCCESS_THRESHOLD,
            "criterion_resolution_check": {
                "median_se_of_yearly_log_ratio": MEDIAN_SE_OF_YEARLY_LOG_RATIO,
                "threshold_in_median_se_units": SUCCESS_THRESHOLD
                / MEDIAN_SE_OF_YEARLY_LOG_RATIO,
                "resolvable": SUCCESS_THRESHOLD > MEDIAN_SE_OF_YEARLY_LOG_RATIO,
            },
            "stop_rule": (
                "If neither hybrid meets the criterion, A-18 stops after Step 1. "
                "Sections 4 and 5.1/5.3 are not run; section 5.2 is exempt."
            ),
            "equivalence_control": (
                "The parameterised transmission copy must reproduce production "
                "deterministic and production stochastic annual outputs exactly "
                "over 1975-2025 before any hybrid result is read."
            ),
        },
        "git_commit": git_text("rev-parse", "HEAD"),
        "git_dirty": bool(status),
        "python": sys.version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "platform": platform.platform(),
        "input_sha256": {
            "model_config": sha256(config_path),
            "target_register": sha256(register_path),
            "run_v3.py": sha256(ROOT / "run_v3.py"),
            "engine_v3.py": sha256(ROOT / "engine_v3.py"),
            "transmission_corrected.py": sha256(ROOT / "transmission_corrected.py"),
            "calib_extractor.py": sha256(ROOT / "calib_extractor.py"),
            "stochastic_allocation.py": sha256(ROOT / "stochastic_allocation.py"),
            "counter_based_rng.py": sha256(ROOT / "counter_based_rng.py"),
            "tools/a18_jensen_mechanism.py": sha256(
                ROOT / "tools/a18_jensen_mechanism.py"
            ),
            "tools/run_a18_jensen_mechanism.py": sha256(Path(__file__).resolve()),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    base = json.loads(config_path.read_text(encoding="utf-8"))
    det_seed = int(args.seeds[0])
    timing_rows: list[dict] = []
    started = time.time()

    def timed(label: str, seed: int, **kwargs) -> dict:
        run_started = time.time()
        annual = run_annual(base, seed=seed, **kwargs)
        timing_rows.append(
            {"arm": label, "seed": seed, "elapsed_seconds": time.time() - run_started}
        )
        return annual

    with (output_dir / "runner_stdout.log").open("w", encoding="utf-8") as log:
        # ── Step 0: equivalence controls ──────────────────────────────────
        print("A-18 Step 0: equivalence controls", flush=True)
        prod_det = timed(
            "production_deterministic", det_seed,
            deterministic=True, run_number=1, log=log,
        )
        copy_det = timed(
            "copy_deterministic_control", det_seed,
            deterministic=True, run_number=2, log=log, variant="copy_deterministic",
        )
        prod_sto_one = timed(
            "production_stochastic_control", det_seed,
            deterministic=False, run_number=3, log=log,
        )
        copy_sto_one = timed(
            "copy_stochastic_control", det_seed,
            deterministic=False, run_number=4, log=log, variant="copy_stochastic",
        )
        equivalence = {
            "production_deterministic_sha256": annual_digest(prod_det),
            "copy_deterministic_sha256": annual_digest(copy_det),
            "production_stochastic_seed_%d_sha256" % det_seed: annual_digest(prod_sto_one),
            "copy_stochastic_seed_%d_sha256" % det_seed: annual_digest(copy_sto_one),
        }
        equivalence["deterministic_corner_bit_identical"] = (
            equivalence["production_deterministic_sha256"]
            == equivalence["copy_deterministic_sha256"]
        )
        equivalence["stochastic_corner_bit_identical"] = (
            equivalence["production_stochastic_seed_%d_sha256" % det_seed]
            == equivalence["copy_stochastic_seed_%d_sha256" % det_seed]
        )
        (raw_dir / "step0_equivalence_control.json").write_text(
            json.dumps(equivalence, indent=2) + "\n", encoding="utf-8"
        )
        if not (
            equivalence["deterministic_corner_bit_identical"]
            and equivalence["stochastic_corner_bit_identical"]
        ):
            raise AssertionError(
                "A-18 parameterised transmission copy is not equivalent to "
                "production; no hybrid result may be read. See "
                "raw/step0_equivalence_control.json"
            )

        # ── Step 1: reference and hybrid ensembles ────────────────────────
        arms: dict[str, list[dict]] = {}
        for arm, variant, deterministic in [
            ("stochastic_reference", None, False),
            ("hybrid_contacts", "hybrid_contacts", True),
            ("hybrid_transmission", "hybrid_transmission", True),
        ]:
            rows: list[dict] = []
            for index, seed in enumerate(args.seeds, start=1):
                annual = timed(
                    arm, seed,
                    deterministic=deterministic, run_number=100 + index, log=log,
                    variant=variant,
                )
                rows.extend(annual_rows(annual, seed=seed, arm=arm))
                print(
                    f"A-18 {arm} {index}/{len(args.seeds)} seed={seed}", flush=True
                )
            arms[arm] = rows

        # ── Step 1e: traced deterministic run for the arithmetic check ────
        print("A-18 Step 1e: traced deterministic run", flush=True)
        trace = TransmissionTrace(trace_years=frozenset(float(y) for y in args.trace_years))
        timed(
            "traced_deterministic", det_seed,
            deterministic=True, run_number=999, log=log,
            variant="copy_deterministic", trace=trace,
        )

        # ── Step 1f: the "did the knob turn?" control ─────────────────────
        # A hybrid that makes no difference is only informative if the thing
        # it changes demonstrably changed.  Trace the hybrid and show that the
        # per-cell contact counts really are integers drawn away from their
        # expectation.
        print("A-18 Step 1f: hybrid knob control", flush=True)
        hybrid_trace = TransmissionTrace(
            trace_years=frozenset(float(y) for y in args.trace_years)
        )
        timed(
            "traced_hybrid_contacts", det_seed,
            deterministic=True, run_number=998, log=log,
            variant="hybrid_contacts", trace=hybrid_trace,
        )

    # ── scoring ───────────────────────────────────────────────────────────
    reference = pd.DataFrame(arms["stochastic_reference"])
    det_frame = pd.DataFrame(annual_rows(prod_det, seed=det_seed, arm="deterministic"))

    candidates = {
        "deterministic_baseline": det_frame[["metric", "year", "value"]],
        "hybrid_contacts": ensemble_mean_frame(arms["hybrid_contacts"], "hybrid_contacts")[
            ["metric", "year", "value"]
        ],
        "hybrid_transmission": ensemble_mean_frame(
            arms["hybrid_transmission"], "hybrid_transmission"
        )[["metric", "year", "value"]],
    }

    scored_frames = []
    summaries = []
    full_frames = []
    for label, frame in candidates.items():
        comparison = compare_annual_levels(frame, reference)
        comparison["arm"] = label
        full_frames.append(comparison)
        keep, summary = score_candidate(frame, reference, target_years, label=label)
        scored_frames.append(keep)
        summaries.append(summary)

    identical_to_deterministic = {}
    det_lookup = det_frame.set_index(["metric", "year"])["value"]
    for label, frame in candidates.items():
        if label == "deterministic_baseline":
            continue
        merged = frame.set_index(["metric", "year"])["value"]
        aligned = merged.reindex(det_lookup.index)
        identical_to_deterministic[label] = {
            "n_metric_years": int(len(det_lookup)),
            "n_identical": int((aligned.to_numpy() == det_lookup.to_numpy()).sum()),
            "bit_identical_to_deterministic": bool(
                np.array_equal(aligned.to_numpy(), det_lookup.to_numpy())
            ),
            "max_abs_difference": float(
                np.max(np.abs(aligned.to_numpy() - det_lookup.to_numpy()))
            ),
        }

    scored = pd.concat(scored_frames, ignore_index=True)
    full = pd.concat(full_frames, ignore_index=True)
    summary_frame = pd.DataFrame(summaries)

    baseline_median = float(
        summary_frame.loc[
            summary_frame["arm"] == "deterministic_baseline", "median_abs_log_ratio"
        ].iloc[0]
    )

    # ── the direct arithmetic check ───────────────────────────────────────
    trace_frame = trace.frame()
    hybrid_trace_frame = hybrid_trace.frame()
    knob = hybrid_trace_frame["n_contacts_cell"]
    knob_control = {
        "traced_strata": int(len(hybrid_trace_frame)),
        "all_contact_counts_integral": bool(
            np.all(knob.to_numpy() == np.floor(knob.to_numpy()))
        ),
        "n_strata_where_drawn_differs_from_expected": int(
            (hybrid_trace_frame["n_contacts_cell"]
             != hybrid_trace_frame["contacts_float"]).sum()
        ),
        "max_abs_deviation_from_expectation": float(
            (hybrid_trace_frame["n_contacts_cell"]
             - hybrid_trace_frame["contacts_float"]).abs().max()
        ),
        "sum_drawn_contacts": float(hybrid_trace_frame["n_contacts_cell"].sum()),
        "sum_expected_contacts": float(hybrid_trace_frame["contacts_float"].sum()),
        "deterministic_control_contacts_all_integral": bool(
            np.all(
                trace_frame["n_contacts_cell"].to_numpy()
                == np.floor(trace_frame["n_contacts_cell"].to_numpy())
            )
        ),
    }
    bias_detail = hypothesised_form_bias(
        trace_frame["contacts_float"].to_numpy(),
        trace_frame["lambda_per_contact"].to_numpy(),
        trace_frame["susceptible_pop"].to_numpy(),
    )
    bias_detail["year"] = trace_frame["year"].to_numpy()
    bias_detail["step"] = trace_frame["step"].to_numpy()
    per_step = per_step_bias_summary(trace_frame)

    # Monte Carlo confirmation of the model-form zero, on the traced stratum
    # carrying the largest expected infection count.
    hottest = trace_frame.assign(
        expected=trace_frame["contacts_float"] * trace_frame["lambda_per_contact"]
    ).sort_values("expected", ascending=False).iloc[0]
    monte_carlo = monte_carlo_model_form_bias(
        float(hottest["contacts_float"]), float(hottest["lambda_per_contact"])
    )
    monte_carlo["stratum"] = {
        key: (int(hottest[key]) if key != "year" else float(hottest[key]))
        for key in ("year", "step", "orient_to", "act_to", "orient_from",
                    "act_from", "age_idx", "prep_idx")
    }

    finite_step_bias = per_step["hypothesised_form_relative_bias_per_step"].replace(
        [np.inf, -np.inf], np.nan
    ).dropna()
    arithmetic = {
        "model_functional_form": (
            "expected infections per cell = n_contacts * sum_stage sum_art "
            "prevalence * p_transmission; affine in n_contacts, second "
            "derivative identically zero"
        ),
        "model_form_jensen_bias": 0.0,
        "model_form_jensen_bias_is_exact_zero": True,
        "monte_carlo_confirmation": monte_carlo,
        "hypothesised_form": "f(m) = 1 - (1 - lambda)^m per susceptible person",
        "hypothesised_form_relative_bias_per_step": {
            "n_steps_traced": int(len(finite_step_bias)),
            "median": float(finite_step_bias.median()) if len(finite_step_bias) else math.nan,
            "min": float(finite_step_bias.min()) if len(finite_step_bias) else math.nan,
            "max": float(finite_step_bias.max()) if len(finite_step_bias) else math.nan,
        },
        "required_per_step_bias_from_b8_signature": 0.0062,
        "trace_years": list(args.trace_years),
        "n_traced_strata": int(len(trace_frame)),
    }

    findings = {
        "equivalence_control_passed": True,
        "hybrid_knob_control": knob_control,
        "hybrid_vs_deterministic_identity": identical_to_deterministic,
        "baseline_median_abs_log_ratio_recomputed": baseline_median,
        "brief_quoted_baseline": BASELINE_MEDIAN_ABS_LOG_RATIO,
        "success_threshold": SUCCESS_THRESHOLD,
        "arm_results": {
            row["arm"]: {
                "median_abs_log_ratio": row["median_abs_log_ratio"],
                "meets_criterion": row["meets_pre_specified_criterion"],
                "median_pct_difference": row["median_pct_difference"],
                "n_years_above_reference": row["n_years_candidate_above_reference"],
                "n_scored_target_years": row["n_scored_target_years"],
            }
            for row in summaries
        },
        "step1_confirms_jensen": bool(
            any(
                row["meets_pre_specified_criterion"]
                for row in summaries
                if row["arm"] != "deterministic_baseline"
            )
        ),
        "direct_arithmetic": arithmetic,
    }
    findings["stop_after_step1"] = not findings["step1_confirms_jensen"]

    # ── artefacts ─────────────────────────────────────────────────────────
    pd.DataFrame(
        arms["stochastic_reference"] + arms["hybrid_contacts"] + arms["hybrid_transmission"]
    ).to_csv(raw_dir / "step1_replicates_1975_2025.csv", index=False)
    det_frame.to_csv(raw_dir / "step1_deterministic_1975_2025.csv", index=False)
    full.to_csv(raw_dir / "step1_yearly_log_ratio.csv", index=False)
    scored.to_csv(raw_dir / "step1_scored_target_years.csv", index=False)
    summary_frame.to_csv(raw_dir / "step1_arm_summary.csv", index=False)
    trace_frame.to_csv(raw_dir / "step1e_transmission_trace.csv", index=False)
    hybrid_trace_frame.to_csv(raw_dir / "step1f_hybrid_contacts_trace.csv", index=False)
    bias_detail.to_csv(raw_dir / "step1e_jensen_bias_by_stratum.csv", index=False)
    per_step.to_csv(raw_dir / "step1e_jensen_bias_per_step.csv", index=False)
    pd.DataFrame(timing_rows).to_csv(raw_dir / "run_timings.csv", index=False)
    (raw_dir / "findings.json").write_text(
        json.dumps(findings, indent=2, default=float) + "\n", encoding="utf-8"
    )
    (raw_dir / "target_years.json").write_text(
        json.dumps(target_years, indent=2) + "\n", encoding="utf-8"
    )

    manifest.update(
        {
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": time.time() - started,
            "result": findings,
            "equivalence_control": equivalence,
        }
    )
    manifest["output_sha256"] = write_hashes(output_dir)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=float) + "\n", encoding="utf-8"
    )
    hashes = {
        str(path.relative_to(output_dir)): sha256(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS.txt"
    }
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()),
        encoding="utf-8",
    )
    print(f"A-18 Step 1 artifacts written to {output_dir}")
    for row in summaries:
        print(
            f"  {row['arm']:>24}: median |log ratio| = "
            f"{row['median_abs_log_ratio']:.5f}  "
            f"criterion {'MET' if row['meets_pre_specified_criterion'] else 'NOT met'}"
        )
    print(
        "A-18 Step 1 verdict: "
        + ("Jensen CONFIRMED" if findings["step1_confirms_jensen"] else "Jensen NOT confirmed — stop rule fires")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
