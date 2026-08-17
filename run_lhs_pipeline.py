"""
Run the staged LHS calibration pipeline with automatic handoffs.

The pipeline runs:
  backbone -> diagnosis -> art -> joint

Each stage receives the best-fit config from the previous stage. Progress is
written to a pipeline status JSON file so long runs can be resumed after a
failure with --resume.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_target_loader import default_disease_workbook_path, default_target_mapping_path
from run_lhs_calibration import run_lhs_calibration


PIPELINE_STAGES = ("backbone", "diagnosis", "art", "joint")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def stage_output_dir(base_dir: Path, stage: str) -> Path:
    return base_dir / f"lhs_{stage}"


def status_path(base_dir: Path) -> Path:
    return base_dir / "lhs_pipeline_status.json"


def completed_best_config(status: dict, stage: str) -> str | None:
    configs = completed_best_configs(status, stage)
    return configs[0] if configs else None


def completed_best_configs(status: dict, stage: str) -> list[str]:
    stage_status = status.get("stages", {}).get(stage, {})
    if stage_status.get("status") != "completed":
        return []
    raw_configs = stage_status.get("best_fit_configs")
    if not raw_configs:
        best_config = stage_status.get("best_fit_config")
        raw_configs = [best_config] if best_config else []
    return [config for config in raw_configs if config and Path(config).exists()]


def next_configs_for_stage(initial_config: str, status: dict, stage: str, beam_width: int) -> list[str]:
    idx = PIPELINE_STAGES.index(stage)
    if idx == 0:
        return [initial_config]
    previous_stage = PIPELINE_STAGES[idx - 1]
    previous_best = completed_best_configs(status, previous_stage)[:beam_width]
    if not previous_best:
        raise RuntimeError(
            f"Cannot start '{stage}': previous stage '{previous_stage}' has no completed best-fit config."
        )
    return previous_best


def build_stage_args(
    args: argparse.Namespace,
    stage: str,
    config: str,
    output_dir: Path | None = None,
    seed: int | None = None,
) -> argparse.Namespace:
    stage_idx = PIPELINE_STAGES.index(stage)
    n = args.joint_n if stage == "joint" and args.joint_n is not None else args.n
    return argparse.Namespace(
        config=config,
        n=n,
        seed=args.seed + stage_idx if seed is None else seed,
        workers=args.workers,
        output_dir=str(output_dir or stage_output_dir(Path(args.output_dir).resolve(), stage)),
        top_n=args.top_n,
        calib_space=args.calib_space,
        unaids_data=args.unaids_data,
        targets_workbook=args.targets_workbook,
        target_mapping=args.target_mapping,
        stage=stage,
        legacy_unaids_targets=args.legacy_unaids_targets,
        skip_het_sensitivity=args.skip_het_sensitivity or stage != "backbone",
        deterministic_calibration=args.deterministic_calibration,
        family_balanced_score=args.family_balanced_score,
        refine_around_input=args.joint_refine_around_input if stage == "joint" else 0.0,
        rerank_top=args.rerank_top if stage == "joint" else 0,
        rerank_replicates=args.rerank_replicates,
        recent_fit_audit=args.recent_fit_audit,
        recent_fit_gate=args.recent_fit_gate if stage == "joint" else False,
        recent_fit_min_observed_fraction=args.recent_fit_min_observed_fraction,
        recent_fit_sigma_multiplier=args.recent_fit_sigma_multiplier,
        recent_fit_diagnosis_year=args.recent_fit_diagnosis_year,
        acceptance_gate_audit=args.acceptance_gate_audit if stage == "joint" else False,
        acceptance_gate=args.acceptance_gate if stage == "joint" else False,
        acceptance_diagnosis_floor=args.acceptance_diagnosis_floor,
        acceptance_diagnosis_ceiling=args.acceptance_diagnosis_ceiling,
        acceptance_plhiv_recent_mape_ceiling=args.acceptance_plhiv_recent_mape_ceiling,
        acceptance_incidence_recent_ratio_floor=args.acceptance_incidence_recent_ratio_floor,
        acceptance_incidence_recent_ratio_ceiling=args.acceptance_incidence_recent_ratio_ceiling,
        acceptance_aids_bounds_fraction_floor=args.acceptance_aids_bounds_fraction_floor,
        acceptance_art_recent_mape_ceiling=args.acceptance_art_recent_mape_ceiling,
        fail_on_inert_sensitivity=args.fail_on_inert_sensitivity,
    )


def load_or_create_status(args: argparse.Namespace) -> dict:
    path = status_path(Path(args.output_dir).resolve())
    if args.resume and path.exists():
        return read_json(path)
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "running",
        "initial_config": str(Path(args.config).resolve()),
        "output_dir": str(Path(args.output_dir).resolve()),
        "n": args.n,
        "joint_n": args.joint_n,
        "seed": args.seed,
        "workers": args.workers,
        "beam_width": args.beam_width,
        "stages": {},
    }


def mark_stage_running(status: dict, args: argparse.Namespace, stage: str, configs: list[str]) -> None:
    status.setdefault("stages", {})[stage] = {
        "status": "running",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "input_config": configs[0] if len(configs) == 1 else None,
        "input_configs": configs,
        "seed": args.seed + PIPELINE_STAGES.index(stage),
        "n": args.joint_n if stage == "joint" and args.joint_n is not None else args.n,
        "output_dir": str(stage_output_dir(Path(args.output_dir).resolve(), stage)),
        "beam_width": args.beam_width,
    }


def mark_stage_completed(status: dict, stage: str, result: dict) -> None:
    best_fit_configs = result.get("best_fit_configs", [])
    if not best_fit_configs:
        raise RuntimeError(f"Stage '{stage}' completed without writing a best-fit config.")
    status["stages"][stage].update(
        {
            "status": "completed",
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "best_fit_config": best_fit_configs[0],
            "best_fit_configs": best_fit_configs,
            "ranked_csv": result.get("ranked_csv"),
            "excel_summary": result.get("excel_summary"),
            "metadata": result.get("metadata"),
            "normalized_targets": result.get("normalized_targets"),
            "branches": result.get("branches", []),
        }
    )


def _candidate_score(result: dict, rank_idx: int) -> float:
    ranked = result.get("ranked")
    try:
        if ranked is not None and len(ranked) > rank_idx:
            return float(ranked.iloc[rank_idx]["total_ll"])
    except (KeyError, TypeError, ValueError):
        pass
    return float("-inf")


def _candidate_rerank_priority(result: dict, rank_idx: int) -> int:
    ranked = result.get("ranked")
    try:
        if ranked is not None and "rerank_total_ll_mean" in ranked.columns and len(ranked) > rank_idx:
            value = ranked.iloc[rank_idx]["rerank_total_ll_mean"]
            if value is not None and value == value:
                return 1
    except (KeyError, TypeError, ValueError):
        pass
    return 0


def _select_stage_candidates(results: list[dict], beam_width: int) -> list[str]:
    candidates = []
    for branch_idx, result in enumerate(results):
        for rank_idx, path in enumerate(result.get("best_fit_configs", [])):
            candidates.append(
                {
                    "path": path,
                    "score": _candidate_score(result, rank_idx),
                    "rerank_priority": _candidate_rerank_priority(result, rank_idx),
                    "branch_idx": branch_idx,
                    "rank_idx": rank_idx,
                }
            )
    candidates.sort(
        key=lambda item: (
            item["rerank_priority"],
            math.isfinite(item["score"]),
            item["score"],
            -item["branch_idx"],
            -item["rank_idx"],
        ),
        reverse=True,
    )
    return [item["path"] for item in candidates[:beam_width]]


def _latest_file(directory: Path, pattern: str) -> Path | None:
    matches = [path for path in directory.glob(pattern) if path.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _load_completed_branch_result(output_dir: Path, top_n: int) -> dict | None:
    ranked_csv = _latest_file(output_dir, "lhs_ranked_results_*.csv")
    best_fit_configs = sorted(output_dir.glob("lhs_best_fit_rank_*.json"))[:top_n]
    if ranked_csv is None or not best_fit_configs:
        return None

    ranked = pd.read_csv(ranked_csv)
    if ranked.empty or "total_ll" not in ranked.columns:
        return None

    return {
        "best_fit_configs": [str(path) for path in best_fit_configs],
        "ranked_csv": str(ranked_csv),
        "excel_summary": str(_latest_file(output_dir, "lhs_summary_*.xlsx") or ""),
        "metadata": str(_latest_file(output_dir, "lhs_metadata_*.json") or ""),
        "normalized_targets": str(_latest_file(output_dir, "normalized_targets_*.csv") or ""),
        "ranked": ranked,
    }


def run_stage(args: argparse.Namespace, stage: str, input_configs: list[str]) -> dict:
    stage_dir = stage_output_dir(Path(args.output_dir).resolve(), stage)
    stage_seed = args.seed + PIPELINE_STAGES.index(stage)
    branch_results = []

    for branch_idx, config in enumerate(input_configs, start=1):
        if len(input_configs) == 1:
            output_dir = stage_dir
        else:
            output_dir = stage_dir / f"branch_{branch_idx:03d}"
        branch_args = build_stage_args(
            args,
            stage,
            config,
            output_dir=output_dir,
            seed=stage_seed + (branch_idx - 1) * 10_000,
        )
        print(f"Input config: {config}")
        print(f"Output dir: {branch_args.output_dir}")
        print(f"Seed: {branch_args.seed}")
        result = None
        if getattr(args, "resume", False):
            result = _load_completed_branch_result(output_dir, args.top_n)
            if result is not None:
                print(f"Skipping completed branch {branch_idx:03d}: {result['ranked_csv']}")
        if result is None:
            result = run_lhs_calibration(branch_args)
        branch_results.append(result)

    selected_configs = _select_stage_candidates(branch_results, args.beam_width)
    if not selected_configs:
        raise RuntimeError(f"Stage '{stage}' completed without selectable best-fit configs.")

    winning = branch_results[0]
    for result in branch_results:
        if result.get("best_fit_configs") and result["best_fit_configs"][0] == selected_configs[0]:
            winning = result
            break

    return {
        "best_fit_configs": selected_configs,
        "ranked_csv": winning.get("ranked_csv"),
        "excel_summary": winning.get("excel_summary"),
        "metadata": winning.get("metadata"),
        "normalized_targets": winning.get("normalized_targets"),
        "ranked": winning.get("ranked"),
        "branches": [
            {
                "input_config": input_configs[idx],
                "best_fit_configs": result.get("best_fit_configs", []),
                "ranked_csv": result.get("ranked_csv"),
                "excel_summary": result.get("excel_summary"),
                "metadata": result.get("metadata"),
                "normalized_targets": result.get("normalized_targets"),
            }
            for idx, result in enumerate(branch_results)
        ],
    }


def mark_failed(status: dict, stage: str, exc: BaseException) -> None:
    status["status"] = "failed"
    status["failed_stage"] = stage
    status["failed_at"] = datetime.now().isoformat(timespec="seconds")
    status["error"] = f"{type(exc).__name__}: {exc}"
    status["traceback"] = traceback.format_exc()
    status.setdefault("stages", {}).setdefault(stage, {})
    status["stages"][stage].update(
        {
            "status": "failed",
            "failed_at": status["failed_at"],
            "error": status["error"],
        }
    )


def run_pipeline(args: argparse.Namespace) -> dict:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pipeline_status_path = status_path(output_dir)
    status = load_or_create_status(args)
    write_json(pipeline_status_path, status)

    try:
        for stage in PIPELINE_STAGES:
            if args.resume:
                existing_best = completed_best_config(status, stage)
                if existing_best:
                    print(f"Skipping completed stage '{stage}': {existing_best}")
                    continue

            configs = next_configs_for_stage(
                str(Path(args.config).resolve()),
                status,
                stage,
                args.beam_width,
            )
            mark_stage_running(status, args, stage, configs)
            write_json(pipeline_status_path, status)

            print("")
            print(f"=== LHS stage: {stage} ===")

            result = run_stage(args, stage, configs)
            mark_stage_completed(status, stage, result)
            write_json(pipeline_status_path, status)
            print(f"Completed stage '{stage}'. Best config: {status['stages'][stage]['best_fit_config']}")

        status["status"] = "completed"
        status["completed_at"] = datetime.now().isoformat(timespec="seconds")
        status["final_config"] = completed_best_config(status, "joint")
        write_json(pipeline_status_path, status)
        if args.diagnostics:
            from run_lhs_diagnostics import build_diagnostics

            diagnostics_result = build_diagnostics(
                argparse.Namespace(
                    status=str(pipeline_status_path),
                    output_dir=args.diagnostics_output_dir,
                    top_n=args.diagnostics_top_n,
                )
            )
            status["diagnostics"] = diagnostics_result
            write_json(pipeline_status_path, status)
            print(f"Diagnostics Excel: {diagnostics_result['excel_report']}")
            print(f"Diagnostics HTML: {diagnostics_result['html_report']}")
        print("")
        print(f"Pipeline completed. Final config: {status['final_config']}")
        print(f"Pipeline status: {pipeline_status_path}")
        return status
    except BaseException as exc:
        failed_stage = next((stage for stage in PIPELINE_STAGES if status.get("stages", {}).get(stage, {}).get("status") == "running"), "unknown")
        mark_failed(status, failed_stage, exc)
        write_json(pipeline_status_path, status)
        print("")
        print(f"Pipeline failed during stage '{failed_stage}'.")
        print(f"Status written to: {pipeline_status_path}")
        print(f"Error: {status['error']}")
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full staged LHS calibration pipeline.")
    parser.add_argument("--config", default=str(ROOT / "1980_test.json"))
    parser.add_argument("--n", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=-1)
    parser.add_argument("--output-dir", default=str(ROOT / "outputs" / "calibration" / "lhs_pipeline"))
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument(
        "--beam-width",
        type=int,
        default=1,
        help="Number of best-fit configs to carry from each stage into the next stage.",
    )
    parser.add_argument(
        "--joint-n",
        type=int,
        default=None,
        help="Override --n for the final joint stage.",
    )
    parser.add_argument("--calib-space", default=str(ROOT / "configs" / "calib_space.json"))
    parser.add_argument("--unaids-data", default=None)
    parser.add_argument("--targets-workbook", default=str(default_disease_workbook_path(ROOT)))
    parser.add_argument("--target-mapping", default=str(default_target_mapping_path(ROOT)))
    parser.add_argument("--legacy-unaids-targets", action="store_true")
    parser.add_argument(
        "--skip-het-sensitivity",
        action="store_true",
        help="Skip the backbone Heterosexual_Male pre-flight sensitivity check.",
    )
    parser.add_argument(
        "--stochastic-screen",
        dest="deterministic_calibration",
        action="store_false",
        default=True,
        help="Evaluate LHS screen draws stochastically instead of deterministic expected flows.",
    )
    parser.add_argument(
        "--family-balanced-score",
        action="store_true",
        help="Rank workbook targets by mean likelihood per target family.",
    )
    parser.add_argument(
        "--joint-refine-around-input",
        type=float,
        default=0.0,
        help="Narrow joint-stage bounds around each incoming config by this fraction of original ranges.",
    )
    parser.add_argument(
        "--rerank-top",
        type=int,
        default=0,
        help="In the joint stage, rerank this many top screen draws with stochastic replicate means.",
    )
    parser.add_argument(
        "--rerank-replicates",
        type=int,
        default=1,
        help="Stochastic replicate count for joint-stage --rerank-top candidates.",
    )
    parser.add_argument(
        "--no-recent-fit-audit",
        dest="recent_fit_audit",
        action="store_false",
        default=True,
        help="Disable V13.5 recent-fit audit columns for normalized target runs.",
    )
    parser.add_argument(
        "--recent-fit-gate",
        action="store_true",
        help="Apply V13.5 broad recent-fit eligibility gate in the joint stage.",
    )
    parser.add_argument(
        "--recent-fit-min-observed-fraction",
        type=float,
        default=0.75,
        help="Minimum model/observed fraction for recent diagnosis gates.",
    )
    parser.add_argument(
        "--recent-fit-sigma-multiplier",
        type=float,
        default=3.0,
        help="One-sided lower-tail sigma gate for latest observed incidence.",
    )
    parser.add_argument(
        "--recent-fit-diagnosis-year",
        type=int,
        default=None,
        help="Optional fixed diagnosis gate year. Defaults to latest available target year per diagnosis output.",
    )
    parser.add_argument(
        "--acceptance-gate-audit",
        action="store_true",
        help="Add V13.6 acceptance-gate columns to joint-stage LHS draws.",
    )
    parser.add_argument(
        "--acceptance-gate",
        action="store_true",
        help="Prioritize joint-stage draws passing all V13.6 acceptance gates.",
    )
    parser.add_argument("--acceptance-diagnosis-floor", type=float, default=0.90)
    parser.add_argument("--acceptance-diagnosis-ceiling", type=float, default=1.50)
    parser.add_argument("--acceptance-plhiv-recent-mape-ceiling", type=float, default=10.0)
    parser.add_argument("--acceptance-incidence-recent-ratio-floor", type=float, default=0.50)
    parser.add_argument("--acceptance-incidence-recent-ratio-ceiling", type=float, default=1.75)
    parser.add_argument("--acceptance-aids-bounds-fraction-floor", type=float, default=0.40)
    parser.add_argument("--acceptance-art-recent-mape-ceiling", type=float, default=10.0)
    parser.add_argument(
        "--fail-on-inert-sensitivity",
        action="store_true",
        help="Fail if the backbone pre-flight sensitivity check finds an active but inert calibration dimension.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse completed stages from lhs_pipeline_status.json and continue from the first incomplete stage.",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="After a completed pipeline, build Excel and HTML diagnostics reports.",
    )
    parser.add_argument(
        "--diagnostics-output-dir",
        default=None,
        help="Directory for diagnostics reports. Defaults to <output-dir>/diagnostics.",
    )
    parser.add_argument(
        "--diagnostics-top-n",
        type=int,
        default=5,
        help="Number of final-stage top configs to rerun for trajectory diagnostics.",
    )
    args = parser.parse_args(argv)
    if args.n <= 0:
        parser.error("--n must be positive")
    if args.top_n <= 0:
        parser.error("--top-n must be positive")
    if args.beam_width <= 0:
        parser.error("--beam-width must be positive")
    if args.beam_width > args.top_n:
        parser.error("--beam-width cannot exceed --top-n")
    if args.joint_n is not None and args.joint_n <= 0:
        parser.error("--joint-n must be positive")
    if not 0.0 <= args.joint_refine_around_input <= 1.0:
        parser.error("--joint-refine-around-input must be between 0 and 1")
    if args.rerank_top < 0:
        parser.error("--rerank-top must be non-negative")
    if args.rerank_replicates <= 0:
        parser.error("--rerank-replicates must be positive")
    if not 0.0 <= args.recent_fit_min_observed_fraction <= 1.0:
        parser.error("--recent-fit-min-observed-fraction must be between 0 and 1")
    if args.recent_fit_sigma_multiplier <= 0:
        parser.error("--recent-fit-sigma-multiplier must be positive")
    if args.acceptance_gate and not args.acceptance_gate_audit:
        args.acceptance_gate_audit = True
    if not 0.0 <= args.acceptance_diagnosis_floor <= args.acceptance_diagnosis_ceiling:
        parser.error("--acceptance-diagnosis-floor must be non-negative and <= --acceptance-diagnosis-ceiling")
    if not 0.0 <= args.acceptance_aids_bounds_fraction_floor <= 1.0:
        parser.error("--acceptance-aids-bounds-fraction-floor must be between 0 and 1")
    if args.diagnostics_top_n <= 0:
        parser.error("--diagnostics-top-n must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        run_pipeline(parse_args(argv))
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
