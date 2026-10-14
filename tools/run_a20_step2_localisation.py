#!/usr/bin/env python3
"""A-20 Step 2: time and state-space localisation of the winning arm."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import shlex
import subprocess
import sys
import time
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
from tools.a20_process_decomposition import ProcessModeSwitch  # noqa: E402
from tools.run_a20_process_decomposition import (  # noqa: E402
    DEFAULT_SEEDS,
    END_YEAR,
    START_YEAR,
    annual_rows,
    configured,
    parse_seeds,
    sha256,
)


class StateMarginalRecorder(AbstractContextManager):
    """Record year-end PLHIV marginals without changing model calculations."""

    def __init__(self, *, arm: str, seed: int):
        self.arm = arm
        self.seed = int(seed)
        self.rows = []

    def __enter__(self):
        original = SimulationEngineV3.record_outputs
        recorder = self

        def wrapped(engine, step, year, *args, **kwargs):
            result = original(engine, step, year, *args, **kwargs)
            # Annual extraction uses the final quarterly row in each integer year.
            if not np.isclose(float(year) % 1.0, 0.75, atol=1e-12):
                return result
            pop = engine.state.population
            positive = pop[:, :, :, 1:, :, :, :]
            axes = {
                "age": (positive.sum(axis=(1, 2, 3, 4, 5, 6)), engine.groups.age_groups),
                "orientation": (
                    positive.sum(axis=(0, 2, 3, 4, 5, 6)),
                    engine.groups.orientations,
                ),
                "activity": (
                    positive.sum(axis=(0, 1, 3, 4, 5, 6)),
                    engine.groups.activities,
                ),
                "hiv_stage": (
                    positive.sum(axis=(0, 1, 2, 4, 5, 6)),
                    [name for name in engine.groups.hiv_stages if name != "Susceptible"],
                ),
                "diagnosis_status": (
                    positive.sum(axis=(0, 1, 2, 3, 5, 6)),
                    engine.groups.diagnosis_states,
                ),
                "art_status": (
                    positive.sum(axis=(0, 1, 2, 3, 4, 6)),
                    engine.groups.art_states,
                ),
            }
            total = int(positive.sum())
            for axis, (values, labels) in axes.items():
                values = np.asarray(values).reshape(-1)
                if len(values) != len(labels) or int(values.sum()) != total:
                    raise AssertionError(f"invalid A-20 {axis} marginal")
                for label, value in zip(labels, values):
                    recorder.rows.append(
                        {
                            "arm": recorder.arm,
                            "seed": recorder.seed,
                            "year": int(year),
                            "axis": axis,
                            "stratum": str(label),
                            "plhiv": int(value),
                        }
                    )
            return result

        self._original = original
        SimulationEngineV3.record_outputs = wrapped
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        SimulationEngineV3.record_outputs = self._original
        return False


def run_one(base, *, arm, seed, forced, deterministic, log):
    config = configured(base, deterministic=deterministic, seed=seed)
    buffer = io.StringIO()
    switch = (
        ProcessModeSwitch(forced) if forced is not None else contextlib.nullcontext()
    )
    recorder = StateMarginalRecorder(arm=arm, seed=seed)
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        with recorder:
            with switch:
                result = SimulationEngineV3(config, seed=seed).run(run_number=seed)
    log.write(f"===== {arm} seed={seed} =====\n{buffer.getvalue()}")
    annual = extract_annual_outputs(result, start_year=START_YEAR, end_year=END_YEAR)
    return recorder.rows, annual


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--step1-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", type=parse_seeds, default=list(DEFAULT_SEEDS))
    args = parser.parse_args()
    if list(args.seeds) != list(DEFAULT_SEEDS):
        raise ValueError("A-20 Step 2 seed set is fixed at 7001-7050")

    config_path = args.model_config.resolve()
    step1_dir = args.step1_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir()

    summary = pd.read_csv(step1_dir / "raw/step1_arm_summary.csv")
    process_rows = summary[~summary["arm"].isin(["deterministic_baseline", "transmission_a18"])]
    winners = process_rows[process_rows["material"]]
    if winners["arm"].tolist() != ["background_mortality"]:
        raise AssertionError(
            f"Step 2 expected the measured winner background_mortality; got {winners['arm'].tolist()}"
        )

    status = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.rstrip()
    manifest = {
        "task": "A-20 Step 2 winner localisation",
        "command": shlex.join([sys.executable, *sys.argv]),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "winner": "background_mortality",
        "seeds": list(args.seeds),
        "n_replicates_per_arm": len(args.seeds),
        "arms": ["background_mortality", "stochastic_reference"],
        "window": [START_YEAR, END_YEAR],
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip(),
        "git_dirty": bool(status),
        "input_sha256": {
            "model_config": sha256(config_path),
            "step1_arm_summary": sha256(step1_dir / "raw/step1_arm_summary.csv"),
            "step1_arm_replicates": sha256(
                step1_dir / "raw/step1_arm_replicates_1975_2025.csv"
            ),
            "run_v3.py": sha256(ROOT / "run_v3.py"),
            "a20_switch": sha256(ROOT / "tools/a20_process_decomposition.py"),
            "runner": sha256(Path(__file__).resolve()),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    base = json.loads(config_path.read_text(encoding="utf-8"))
    rows = []
    annual_rows_out = []
    timings = []
    started = time.time()
    with (output_dir / "runner_stdout.log").open("w", encoding="utf-8") as log:
        for arm, deterministic, forced in (
            ("background_mortality", True, frozenset({"background_mortality"})),
            ("stochastic_reference", False, None),
        ):
            for index, seed in enumerate(args.seeds, start=1):
                t0 = time.time()
                state_rows, annual = run_one(
                    base,
                    arm=arm,
                    seed=seed,
                    forced=forced,
                    deterministic=deterministic,
                    log=log,
                )
                rows.extend(state_rows)
                annual_rows_out.extend(annual_rows(annual, arm=arm, seed=seed))
                timings.append(
                    {"arm": arm, "seed": seed, "elapsed_seconds": time.time() - t0}
                )
                print(f"A-20 Step 2 {arm} {index}/50 seed={seed}", flush=True)

    states = pd.DataFrame(rows)
    annual = pd.DataFrame(annual_rows_out)
    states.to_csv(raw_dir / "state_marginals_by_seed.csv", index=False)
    annual.to_csv(raw_dir / "step2_annual_replicates.csv", index=False)
    pd.DataFrame(timings).to_csv(raw_dir / "run_timings.csv", index=False)

    # Prove the rerun reproduces Step 1's winning-arm annual trajectories.
    step1 = pd.read_csv(step1_dir / "raw/step1_arm_replicates_1975_2025.csv")
    step1 = step1[step1["arm"] == "background_mortality"]
    check = annual[annual["arm"] == "background_mortality"].merge(
        step1,
        on=["arm", "seed", "metric", "year"],
        suffixes=("_step2", "_step1"),
    )
    reproduction = {
        "n_rows": int(len(check)),
        "max_absolute_difference": float(
            (check["value_step2"] - check["value_step1"]).abs().max()
        ),
    }
    if reproduction != {"n_rows": 17850, "max_absolute_difference": 0.0}:
        raise AssertionError(f"Step 2 does not reproduce Step 1: {reproduction}")
    (raw_dir / "step1_reproduction.json").write_text(
        json.dumps(reproduction, indent=2) + "\n", encoding="utf-8"
    )

    means = states.groupby(["arm", "year", "axis", "stratum"], as_index=False)[
        "plhiv"
    ].mean()
    wide = means.pivot(
        index=["year", "axis", "stratum"], columns="arm", values="plhiv"
    ).reset_index()
    wide["difference_winner_minus_stochastic"] = (
        wide["background_mortality"] - wide["stochastic_reference"]
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        wide["pct_difference_vs_stochastic"] = np.where(
            wide["stochastic_reference"] != 0,
            100.0 * wide["difference_winner_minus_stochastic"]
            / wide["stochastic_reference"],
            np.nan,
        )
    totals = (
        wide.groupby(["year", "axis"])["difference_winner_minus_stochastic"]
        .sum()
        .rename("total_plhiv_difference")
        .reset_index()
    )
    wide = wide.merge(totals, on=["year", "axis"])
    with np.errstate(divide="ignore", invalid="ignore"):
        wide["share_of_total_difference"] = np.where(
            wide["total_plhiv_difference"] != 0,
            wide["difference_winner_minus_stochastic"]
            / wide["total_plhiv_difference"],
            np.nan,
        )
    wide.to_csv(raw_dir / "state_space_displacement.csv", index=False)

    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["elapsed_seconds"] = time.time() - started
    manifest["status"] = "complete"
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
    print(json.dumps({"reproduction": reproduction, "elapsed_seconds": manifest["elapsed_seconds"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
