#!/usr/bin/env python3
"""Run A-19: screen Sainte-Lague, D'Hondt and controlled rounding.

Implements TASK_BRIEF_DIVISOR_AND_CONTROLLED_ROUNDING_2026-09-12.md.  No
epidemic model is run -- this is the cheap synthetic screen the rounding-method
memo (section 5) requires before any model run is spent on a candidate.
"""

from __future__ import annotations

import hashlib
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

from tools.a19_allocation_candidates import (  # noqa: E402
    ALL_RULES,
    exact_trajectory,
    margin_divergence,
    layout_margin_diagnostics,
    rare_event_screen,
    run_path,
    synthetic_design,
    trajectory_screen,
)

#: Section 3 of the brief.  A-17's published figures, to the precision printed
#: in the memo.  The harness must reproduce these before anything else is read.
VALIDITY_GATE = {
    "largest_remainder": -0.0012772,
    "stochastic_rounding": 0.0000093,
    "rotating_systematic": -0.0130858,
    "fully_cellwise_rounding": -0.0153720,
}
GATE_TOLERANCE = 5e-8

#: Section 4 of the brief, pre-committed before any candidate was run.
C2_INCUMBENT_DRIFT = 0.0012772156710760818
C1_WORST_ERROR_THRESHOLD = 0.10


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frame_digest(frame: pd.DataFrame) -> str:
    return hashlib.sha256(frame.to_csv(index=False).encode("utf-8")).hexdigest()


def git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def run_validity_gate(steps: int, stochastic_seeds: int) -> tuple[pd.DataFrame, bool]:
    """Re-run A-17's five rules on the A-19 2-D harness and check the published figures."""
    design = synthetic_design(steps=steps)
    initial, rates, shape = design["initial"], design["rates"], design["shape"]
    exact_final = exact_trajectory(initial, rates, steps)[:, -1]

    rows: list[dict] = []
    for rule, published in VALIDITY_GATE.items():
        if rule == "stochastic_rounding":
            ensemble = np.empty((stochastic_seeds, initial.size), dtype=float)
            for seed in range(1, stochastic_seeds + 1):
                _, cells = run_path(rule, initial, rates, steps, shape=shape, seed=seed)
                ensemble[seed - 1] = cells
            final = ensemble.mean(axis=0)
        else:
            _, cells = run_path(rule, initial, rates, steps, shape=shape, seed=1)
            final = cells.astype(float)
        measured = float(final.sum() / exact_final.sum() - 1.0)
        rows.append(
            {
                "rule": rule,
                "a17_published_relative_divergence": published,
                "a19_measured_relative_divergence": measured,
                "absolute_difference": abs(measured - published),
                "reproduces": abs(measured - published) <= GATE_TOLERANCE,
            }
        )
    frame = pd.DataFrame(rows)
    return frame, bool(frame["reproduces"].all())


def main() -> int:
    started = time.time()
    command = shlex.join([sys.executable, *sys.argv])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "outputs" / "calibration" / f"a19_divisor_controlled_rounding_{stamp}"
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "provenance").mkdir(exist_ok=True)

    steps, stochastic_seeds = 300, 500

    # ---- Section 3: validity gate, before any candidate result is read -----
    gate_start = time.time()
    gate, gate_passed = run_validity_gate(steps, stochastic_seeds)
    gate.to_csv(raw_dir / "gate_a17_reproduction.csv", index=False)
    gate_seconds = time.time() - gate_start
    print(f"A-19 validity gate: {'PASS' if gate_passed else 'FAIL'} ({gate_seconds:.1f}s)")
    if not gate_passed:
        (raw_dir / "findings.json").write_text(
            json.dumps({"validity_gate_passed": False}, indent=2) + "\n", encoding="utf-8"
        )
        raise SystemExit("A-19 validity gate failed; brief section 6 requires stopping")

    # ---- C2: drift from the exact fractional trajectory --------------------
    timings: list[dict] = []
    design = synthetic_design(steps=steps)
    initial, rates, shape = design["initial"], design["rates"], design["shape"]
    for rule in ALL_RULES:
        rule_start = time.time()
        seeds = stochastic_seeds if rule == "stochastic_rounding" else 1
        for seed in range(1, seeds + 1):
            run_path(rule, initial, rates, steps, shape=shape, seed=seed)
        timings.append(
            {
                "rule": rule,
                "n_paths": seeds,
                "seconds": time.time() - rule_start,
                "seconds_per_path": (time.time() - rule_start) / seeds,
            }
        )
    pd.DataFrame(timings).to_csv(raw_dir / "runtime.csv", index=False)

    trajectories, trajectory_meta = trajectory_screen(
        steps=steps, stochastic_seeds=stochastic_seeds
    )
    trajectories["passes_c2"] = (
        trajectories["final_relative_divergence"].abs() <= C2_INCUMBENT_DRIFT + 1e-12
    )
    trajectories.to_csv(raw_dir / "c2_trajectory_divergence.csv", index=False)

    # ---- C1: rare-event compositional screen -------------------------------
    rare_summary, rare_deciles = rare_event_screen(seeds=stochastic_seeds)
    rare_summary.to_csv(raw_dir / "c1_rare_event_summary.csv", index=False)
    rare_deciles.to_csv(raw_dir / "c1_rare_event_deciles.csv", index=False)
    margins = layout_margin_diagnostics()
    margins.to_csv(raw_dir / "c1_layout_margins.csv", index=False)

    # ---- C3: fixed-configuration reproducibility ---------------------------
    repeat_trajectories, _ = trajectory_screen(steps=steps, stochastic_seeds=stochastic_seeds)
    repeat_rare, _ = rare_event_screen(seeds=stochastic_seeds)
    reproducibility = {
        "c2_run1_sha256": frame_digest(trajectories.drop(columns="passes_c2")),
        "c2_run2_sha256": frame_digest(repeat_trajectories),
        "c1_run1_sha256": frame_digest(rare_summary),
        "c1_run2_sha256": frame_digest(repeat_rare),
    }
    reproducibility["c2_reproducible"] = (
        reproducibility["c2_run1_sha256"] == reproducibility["c2_run2_sha256"]
    )
    reproducibility["c1_reproducible"] = (
        reproducibility["c1_run1_sha256"] == reproducibility["c1_run2_sha256"]
    )
    reproducibility["passes_c3"] = bool(
        reproducibility["c2_reproducible"] and reproducibility["c1_reproducible"]
    )
    (raw_dir / "c3_reproducibility.json").write_text(
        json.dumps(reproducibility, indent=2) + "\n", encoding="utf-8"
    )

    # ---- Verdict table ------------------------------------------------------
    verdict_rows: list[dict] = []
    for _, row in trajectories.iterrows():
        rule = row["rule"]
        c1_candidates = rare_summary[
            rare_summary["rule"].str.startswith(rule)
        ]
        for _, c1 in c1_candidates.iterrows():
            verdict_rows.append(
                {
                    "candidate": c1["rule"],
                    "family": row["family"],
                    "c1_zero_deciles": int(c1["n_scored_deciles_with_zero_allocation"]),
                    "c1_worst_decile_abs_relative_error": float(
                        c1["worst_scored_decile_abs_relative_error"]
                    ),
                    "passes_c1": bool(c1["passes_c1"]),
                    "c2_final_relative_divergence": float(row["final_relative_divergence"]),
                    "passes_c2": bool(row["passes_c2"]),
                    "passes_c3": reproducibility["passes_c3"],
                    "recommended_for_switch_grid": bool(
                        c1["passes_c1"] and row["passes_c2"] and reproducibility["passes_c3"]
                    ),
                    "max_abs_row_margin_relative_error": float(
                        row["max_abs_row_margin_relative_error"]
                    ),
                    "max_abs_col_margin_relative_error": float(
                        row["max_abs_col_margin_relative_error"]
                    ),
                }
            )
    verdict = pd.DataFrame(verdict_rows)
    verdict.to_csv(raw_dir / "verdict.csv", index=False)

    findings = {
        "task": "A-19 divisor and controlled-rounding screen",
        "c1_layout_margins": margins.to_dict(orient="records"),
        "validity_gate_passed": gate_passed,
        "c1_threshold_worst_decile_abs_relative_error": C1_WORST_ERROR_THRESHOLD,
        "c2_threshold_incumbent_abs_drift": C2_INCUMBENT_DRIFT,
        "c1": rare_summary.to_dict(orient="records"),
        "c2": trajectories.to_dict(orient="records"),
        "c3": reproducibility,
        "n_candidates_recommended": int(verdict["recommended_for_switch_grid"].sum()),
        "recommended": verdict.loc[
            verdict["recommended_for_switch_grid"], "candidate"
        ].tolist(),
    }
    (raw_dir / "findings.json").write_text(
        json.dumps(findings, indent=2, default=float) + "\n", encoding="utf-8"
    )

    (out_dir / "provenance" / "git_status_short.txt").write_text(
        git("status", "--short") + "\n", encoding="utf-8"
    )
    (out_dir / "provenance" / "git_diff.patch").write_text(git("diff") + "\n", encoding="utf-8")

    manifest = {
        "task": "A-19 divisor and controlled-rounding screen",
        "brief": "TASK_BRIEF_DIVISOR_AND_CONTROLLED_ROUNDING_2026-09-12.md",
        "implements_memo_section": "ROUNDING_METHOD_LITERATURE_COMPARISON_2026-09-12.md section 4",
        "command": command,
        "started_at_utc": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.time() - started,
        "model_runs": 0,
        "production_behaviour_changed": False,
        "pre_specification": {
            "c1": (
                "Epidemic-shaped rare-event regime (600 cells at n=9900, 600 at n=100, "
                "p=1e-4): no size decile with expected events >= 1 may receive zero, and "
                "the worst scored decile relative error must be below 0.10."
            ),
            "c2": (
                "Absolute final relative divergence from the exact fractional trajectory "
                "over 300 steps must not exceed the incumbent's 0.0012772."
            ),
            "c3": "Two runs at fixed configuration must produce identical output hashes.",
            "validity_gate": (
                "The 2-D harness must reproduce A-17's four published divergence figures "
                "before any candidate result is read."
            ),
            "registered_predictions": [
                "D'Hondt fails C1 at least as badly as largest remainder.",
                "Sainte-Lague also fails C1, because any size-monotone deterministic rule "
                "must hand all T units to the T largest expectations when every expectation "
                "is below 1.",
                "Controlled rounding passes C1 only at the granularity of the margins given.",
            ],
        },
        "design": trajectory_meta,
        "rules": list(ALL_RULES),
        "validity_gate_passed": gate_passed,
        "validity_gate_seconds": gate_seconds,
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--short")),
        "python": sys.version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "platform": platform.platform(),
        "input_sha256": {
            name: sha256(ROOT / name)
            for name in (
                "tools/a19_allocation_candidates.py",
                "tools/run_a19_allocation_screen.py",
                "tools/a17_residual_placement.py",
                "counter_based_rng.py",
                "stochastic_allocation.py",
            )
        },
        "result": findings,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=float) + "\n", encoding="utf-8"
    )
    hash_paths = sorted(
        path for path in out_dir.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    (out_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.relative_to(out_dir)}\n" for path in hash_paths),
        encoding="utf-8",
    )

    print(f"\nA-19 artifacts written to {out_dir}")
    print("\nC1 -- rare-event compositional screen:")
    print(
        rare_summary[
            [
                "rule",
                "n_scored_deciles_with_zero_allocation",
                "worst_scored_decile_abs_relative_error",
                "passes_c1",
                "small_stratum_relative_error",
            ]
        ].to_string(index=False)
    )
    print("\nC1 supplementary -- why the layouts differ:")
    print(margins.to_string(index=False))
    print("\nC2 -- drift from the exact fractional trajectory:")
    print(
        trajectories[
            [
                "rule",
                "final_relative_divergence",
                "max_abs_row_margin_relative_error",
                "max_abs_col_margin_relative_error",
                "passes_c2",
            ]
        ].to_string(index=False)
    )
    print(f"\nC3 reproducible: {reproducibility['passes_c3']}")
    print(f"Candidates recommended for the switch grid: {findings['recommended'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
