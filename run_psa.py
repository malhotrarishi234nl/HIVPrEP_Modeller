"""
run_psa.py — PSA entry point for V11 HIV model.

Usage:
    python run_psa.py [--config configs/v11_configuration.json]
                      [--psa-config configs/psa_config.json]
                      [--n-draws N]
                      [--output-dir outputs/psa]
                      [--seed SEED]
                      [--workers W]
                      [--dry-run]
                      [--quiet]

Options:
    --config        Path to model config JSON (default: configs/v11_configuration.json)
    --psa-config    Path to PSA config JSON (default: configs/psa_config.json)
    --n-draws       Override n_draws from psa_config (useful for testing)
    --output-dir    Override output directory
    --seed          Override PSA RNG seed
    --workers       Number of parallel workers (default: 1 = sequential).
                    Set to number of CPU cores for max throughput.
                    Use 0 for auto-detect (os.cpu_count()).
    --dry-run       Run pre-flight checks and parameter discovery only; do not simulate
    --quiet         Suppress per-draw engine terminal output (recommended for large N)

V11.10.3 — 2026-04-27
V11.10.6 — 2026-04-27 — Added --workers for parallel execution

Known limitations (documented):
  - BACKLOG-1: Beta-contact rate collinearity — parameters sampled independently
  - BACKLOG-15: AE placeholder distributions — HTA comparison not defensible
  - BACKLOG-14: Uncalibrated PrEP/Lena schedules
"""

import argparse
import copy
import io
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import pandas as pd


def run_psa(config_path, psa_config_path, n_draws_override=None,
            output_dir_override=None, seed_override=None, dry_run=False,
            quiet=False, workers=1):
    """
    Run Probabilistic Sensitivity Analysis.

    Parameters
    ----------
    config_path : str
        Path to the base model config JSON.
    psa_config_path : str
        Path to the PSA config JSON.
    n_draws_override : int or None
        Override n_draws from psa_config.
    output_dir_override : str or None
        Override output directory.
    seed_override : int or None
        Override PSA RNG seed.
    dry_run : bool
        If True, run pre-flight only (no simulation).
    quiet : bool
        If True, suppress per-draw engine terminal output.
    workers : int
        Number of parallel worker processes.
        1 = sequential (default). 0 = auto-detect (os.cpu_count()).

    Returns
    -------
    dict or None
        {"draws": pd.DataFrame, "outcomes": pd.DataFrame} on success.
        None if dry_run.
    """
    # 1. Load configs
    with open(config_path) as f:
        base_config = json.load(f)
    with open(psa_config_path) as f:
        psa_config = json.load(f)

    # 2. Apply CLI overrides to psa_config (do NOT modify base_config)
    if n_draws_override is not None:
        psa_config["sampling"]["n_draws"]["value"] = n_draws_override
    if output_dir_override is not None:
        psa_config["outputs"]["directory"]["value"] = output_dir_override
    if seed_override is not None:
        psa_config["sampling"]["seed"]["value"] = seed_override

    n = psa_config["sampling"]["n_draws"]["value"]
    seed = psa_config["sampling"]["seed"]["value"]
    output_dir = Path(psa_config["outputs"]["directory"]["value"])

    # Resolve worker count
    if workers == 0:
        workers = os.cpu_count() or 4
    workers = max(1, min(workers, n))  # cap at n draws

    # 3. PSA pre-flight: sampler init runs validate_tier_schema internally
    from psa_sampler import PSASampler
    from psa_metrics import MetricExtractor

    sampler = PSASampler(base_config, psa_config)
    extractor = MetricExtractor(psa_config["metrics"]["extract"])

    print(f"PSA pre-flight OK")
    print(f"  Parameters: {len(sampler.param_paths)}")
    print(f"  Metrics:    {extractor.metric_names()}")
    print(f"  Draws:      {n}")
    print(f"  Method:     {psa_config['sampling']['method']['value'].upper()}")
    print(f"  Seed:       {seed}")
    print(f"  Workers:    {workers}" + (" (sequential)" if workers == 1 else " (parallel)"))

    if dry_run:
        print("\nDRY RUN — stopping before simulation.")
        summary = sampler.get_parameter_summary()
        print("\nParameter summary:")
        print(summary.to_string(index=False))
        return None

    # 4. Deterministic mode enforcement
    if not base_config.get("run_control", {}).get("deterministic_mode", {}).get("value", True):
        print("WARNING: deterministic_mode is False in base config. PSA will force "
              "deterministic_mode=True for all draws to isolate parameter uncertainty.")

    # 5. Generate draws matrix
    draws_df = sampler.generate_draws(n=n, seed=seed)

    # 6. Run simulation loop
    output_dir.mkdir(parents=True, exist_ok=True)

    metric_configs = psa_config["metrics"]["extract"]
    t_start = time.time()

    if workers == 1:
        # ── Sequential path (original behaviour, also used by tests) ────
        all_outcomes, failed_draws = _run_sequential(
            base_config, draws_df, seed, metric_configs, n, quiet, t_start
        )
    else:
        # ── Parallel path ───────────────────────────────────────────────
        try:
            all_outcomes, failed_draws = _run_parallel(
                base_config, draws_df, seed, metric_configs, n, workers, t_start
            )
        except (PermissionError, NotImplementedError) as exc:
            print(
                "\nWARNING: Parallel PSA execution is unavailable in this "
                f"environment ({exc}). Falling back to sequential execution."
            )
            workers = 1
            all_outcomes, failed_draws = _run_sequential(
                base_config, draws_df, seed, metric_configs, n, quiet, t_start
            )

    total_time = time.time() - t_start
    print(f"\nPSA complete: {n} draws in {total_time:.1f}s "
          f"({total_time/n:.1f}s per draw, {workers} workers)")

    # 7. Collect results
    outcomes_df = pd.DataFrame(all_outcomes).set_index("draw_index")

    # 8. Write outputs
    from psa_output_writer import PSAOutputWriter
    writer = PSAOutputWriter(
        draws_df=draws_df,
        outcomes_df=outcomes_df,
        sampler=sampler,
        psa_config=psa_config,
        output_dir=output_dir,
        failed_draws=failed_draws,   # NEW optional param
    )
    writer.write_all()

    return {"draws": draws_df, "outcomes": outcomes_df}


# ── Sequential execution ────────────────────────────────────────────────────

def _run_sequential(base_config, draws_df, seed, metric_configs, n, quiet, t_start):
    """Run draws one at a time in the current process."""
    from run_v3 import SimulationEngineV3
    from psa_metrics import MetricExtractor

    extractor = MetricExtractor(metric_configs)
    all_outcomes = []
    failed_draws = []

    for i, (_, draw_row) in enumerate(draws_df.iterrows()):
        try:
            cfg_copy = _prepare_draw_config(base_config, draw_row)

            if quiet:
                with redirect_stdout(io.StringIO()):
                    engine = SimulationEngineV3(cfg_copy, seed=seed + i)
                    result = engine.run()
            else:
                engine = SimulationEngineV3(cfg_copy, seed=seed + i)
                result = engine.run()

            outcomes = extractor.extract(result["summary"])
            outcomes["draw_index"] = i
            all_outcomes.append(outcomes)
            del engine  # F11: explicit cleanup

        except Exception as e:
            import traceback
            failed_draws.append({"draw_index": i, "error": str(e),
                                 "traceback": traceback.format_exc()})
            print(f"\n  WARNING: Draw {i} failed: {e}")

        _report_progress(i, n, t_start)

    if failed_draws:
        print(f"\n  WARNING: {len(failed_draws)}/{n} draws failed. "
              f"Continuing with {len(all_outcomes)} successful draws.")

    return all_outcomes, failed_draws


# ── Parallel execution ──────────────────────────────────────────────────────

def _run_single_draw(args):
    """
    Worker function for parallel execution.
    Must be a top-level function (picklable by multiprocessing).

    Parameters
    ----------
    args : tuple
        (draw_index, draw_dict, base_config, seed, metric_configs)

    Returns
    -------
    dict
        Outcome metrics with draw_index.
    """
    draw_index, draw_dict, base_config, seed, metric_configs = args

    # Imports inside worker — each subprocess needs its own imports
    import io, sys, os
    from contextlib import redirect_stdout

    _dir = os.path.dirname(os.path.abspath(__file__))
    if _dir not in sys.path:      # F10: guard sys.path
        sys.path.insert(0, _dir)

    from run_v3 import SimulationEngineV3
    from psa_metrics import MetricExtractor
    from run_psa import _prepare_draw_config  # reuse shared function

    extractor = MetricExtractor(metric_configs)

    # Convert draw_dict back to a Series-like for _prepare_draw_config
    import pandas as pd
    draw_row = pd.Series(draw_dict)
    cfg_copy = _prepare_draw_config(base_config, draw_row)

    with redirect_stdout(io.StringIO()):
        engine = SimulationEngineV3(cfg_copy, seed=seed + draw_index)
        result = engine.run()

    outcomes = extractor.extract(result["summary"])
    outcomes["draw_index"] = draw_index
    del engine  # F11
    return outcomes


def _run_parallel(base_config, draws_df, seed, metric_configs, n, workers, t_start):
    """Run draws across multiple processes using ProcessPoolExecutor."""
    # Build argument list — convert each row to a plain dict for pickling
    args_list = []
    for i, (_, draw_row) in enumerate(draws_df.iterrows()):
        args_list.append((i, dict(draw_row), base_config, seed, metric_configs))

    all_outcomes = [None] * n  # pre-allocate to preserve order
    completed = 0

    failed_draws = []

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_run_single_draw, args): args[0]
            for args in args_list
        }

        for future in as_completed(futures):
            draw_idx = futures[future]
            try:
                result = future.result()
                all_outcomes[draw_idx] = result
            except Exception as e:
                import traceback
                failed_draws.append({"draw_index": draw_idx, "error": str(e),
                                     "traceback": traceback.format_exc()})
                print(f"\n  WARNING: Draw {draw_idx} failed: {e}")
            completed += 1
            _report_progress(completed - 1, n, t_start)

    # Filter out None entries from pre-allocated list
    all_outcomes = [o for o in all_outcomes if o is not None]
    return all_outcomes, failed_draws


# ── Shared helpers ──────────────────────────────────────────────────────────

def _prepare_draw_config(base_config, draw_row):
    """Deep-copy base config, inject draw values, suppress output."""
    cfg_copy = copy.deepcopy(base_config)
    cfg_copy["run_control"]["deterministic_mode"]["value"] = True
    cfg_copy["output"] = {
        "enabled": {"value": False},
        "terminal_summary": {"value": False},
    }
    for path, value in draw_row.items():
        _inject_value(cfg_copy, path, float(value))
    return cfg_copy


def _report_progress(i, n, t_start):
    """Print progress line."""
    report_interval = 1 if n <= 20 else 10
    if (i + 1) % report_interval == 0 or (i + 1) == n:
        elapsed = time.time() - t_start
        rate = (i + 1) / elapsed if elapsed > 0 else 0
        eta = (n - i - 1) / rate if rate > 0 else 0
        print(f"  Draw {i+1}/{n}  |  {elapsed:.0f}s elapsed  |  "
              f"{rate:.1f} draws/s  |  ETA {eta:.0f}s", end='\r')


def _inject_value(config: dict, dot_path: str, value: float) -> None:
    """
    Set config[a][b][c]["value"] = value given dot_path = "a.b.c".
    The terminal node in the config is expected to be a {"value": ...} wrapper dict.
    Raises KeyError with a descriptive message if any key is missing.
    """
    keys = dot_path.split(".")
    node = config
    for key in keys[:-1]:
        try:
            node = node[key]
        except KeyError:
            raise KeyError(
                f"_inject_value: key '{key}' not found while navigating path "
                f"'{dot_path}'. Available keys at this level: {list(node.keys())}"
            )
    leaf_key = keys[-1]
    if leaf_key not in node:
        raise KeyError(
            f"_inject_value: terminal key '{leaf_key}' not found at path '{dot_path}'."
        )
    if isinstance(node[leaf_key], dict) and "value" in node[leaf_key]:
        node[leaf_key]["value"] = value
    else:
        node[leaf_key] = value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run Probabilistic Sensitivity Analysis for V11 HIV model"
    )
    parser.add_argument("--config", default="configs/v11_configuration.json",
                        help="Path to model config JSON")
    parser.add_argument("--psa-config", default="configs/psa_config.json",
                        help="Path to PSA config JSON")
    parser.add_argument("--n-draws", type=int, default=None,
                        help="Override number of PSA draws")
    parser.add_argument("--output-dir", default=None,
                        help="Override output directory")
    parser.add_argument("--seed", type=int, default=None,
                        help="Override PSA RNG seed")
    parser.add_argument("--dry-run", action="store_true",
                        help="Pre-flight checks only, no simulation")
    parser.add_argument("--workers", type=int, default=1,
                        help="Parallel workers (1=sequential, 0=auto-detect CPU count)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-draw engine output")
    args = parser.parse_args()

    run_psa(
        config_path=args.config,
        psa_config_path=args.psa_config,
        n_draws_override=args.n_draws,
        output_dir_override=args.output_dir,
        seed_override=args.seed,
        dry_run=args.dry_run,
        quiet=args.quiet,
        workers=args.workers,
    )
