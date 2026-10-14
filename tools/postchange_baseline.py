"""
postchange_baseline.py — model-output regression baseline, established 2026-09-14.

WHY THIS EXISTS
---------------
Before this file, **not one test in the repository asserted a model output.** Every
green pin measured something the production changes structurally could not move: RNG
key derivation (the A-13 pins), sampler moments on synthetic inputs (A-28), config
reachability (A-12 guards), static AST structure (A-20), the v6 config golden dict.
Meanwhile every harness that *did* compare model output to a stored reference was
pinned to a pre-A-28 world and is now failing or, worse, silently stale
(`run_a13`, `run_a14`, `run_a15` read stale references without asserting).

So a regression in the model itself would have been caught by nothing.

This module closes that gap. It captures a small, cheap, whole-model fingerprint and
the scalars needed to *diagnose* a mismatch rather than merely detect one.

WHAT THIS IS NOT
----------------
**This baseline is a regression detector, NOT a correctness certificate.** It says
"the model still produces what it produced on 2026-09-14". It says nothing whatever
about whether those numbers are *right*. Several of them are known to be
provisional: the C-1 series reproduces at 101.1% rather than 100% because injected
people transmit before ART; `infections_averted` variance rose 1.58-1.96x; A-3's
closure criterion 1 now fails at 6.57 SE. Pinning current behaviour freezes those
properties in place so they cannot drift unnoticed — it does not bless them.

RELATIONSHIP TO THE OLD REFERENCES
----------------------------------
This is an **addendum collection**. It does not touch, replace or re-baseline any
pre-change reference artefact. A-15's stored outputs, A-16's `validate_reproduction`
gate, A-9's exact controls and the `8d186b35...` digest assertions in
`run_a21_*` / `run_a22_*` are all left failing **deliberately**, as standing flags
that the model has moved. Two baselines coexist on purpose: the old one records what
the model was, this one detects what changes from here.

RE-BASELINING
-------------
`write_baseline()` is the ONLY way to move this baseline, it must be run
deliberately, and it stamps the date and the reason into the artefact. Never
regenerate it to make a failing test pass — that inverts the entire point and is
prohibited by §1A.4. If a test here fails, the correct first assumption is that the
model changed and somebody needs to say why.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calib_extractor import extract_annual_outputs  # noqa: E402
from run_v3 import SimulationEngineV3  # noqa: E402

BASELINE_PATH = ROOT / "tools" / "postchange_baseline.json"
CONFIG_PATH = ROOT / "configs" / "V13.22_20260604_nearfit_rank001_basecase.json"

DETERMINISTIC_SEED = 7001
STOCHASTIC_SEEDS = (42, 43, 44)
START_YEAR = 1975
END_YEAR = 2025

# Deliberately the same seven metrics the pre-change digest used, so the two
# lineages are comparable even though they are stored separately.
METRICS = (
    "PLHIV", "new_diagnoses", "new_diagnoses_msm", "aids_deaths",
    "on_art_stock", "new_infections", "new_aids_cases",
)

# Modules whose content drives model output. Hashed so that a baseline mismatch can
# be ATTRIBUTED rather than merely observed.
TRACKED_MODULES = (
    "run_v3.py", "engine_v3.py", "transmission_corrected.py", "demography.py",
    "migration.py", "economics.py", "counter_based_rng.py",
    "stochastic_allocation.py", "schedule_utils.py", "config_reader.py",
    "config_utils.py", "calib_extractor.py", "exogenous_diagnoses.py",
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def annual_digest(annual: dict) -> str:
    """SHA-256 over the seven pinned annual series. Self-contained by design."""
    payload = json.dumps(
        {m: {str(y): float(v) for y, v in annual[m].items()} for m in METRICS},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _configure(base: dict, seed: int, *, deterministic: bool) -> dict:
    config = json.loads(json.dumps(base))
    config["run_control"]["deterministic_mode"] = {"value": bool(deterministic)}
    config.setdefault("simulation_controls", {})["deterministic_mode"] = {
        "value": bool(deterministic)
    }
    config["run_control"]["seed"] = {"value": int(seed)}
    config["output"]["enabled"]["value"] = False
    config["output"]["terminal_summary"]["value"] = False
    return config


def _run(base: dict, seed: int, *, deterministic: bool):
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        engine = SimulationEngineV3(_configure(base, seed, deterministic=deterministic),
                                    seed=seed)
        return engine.run(run_number=0)


def _scalars(annual: dict, summary) -> dict:
    """Diagnosable scalars. A digest says 'something moved'; these say what."""
    def total(metric: str, lo: int, hi: int) -> float:
        return float(sum(v for y, v in annual.get(metric, {}).items() if lo <= int(y) <= hi))

    def at(metric: str, year: int) -> float:
        return float(annual.get(metric, {}).get(year, 0.0))

    frame = summary.copy()
    frame["yr"] = frame["year"].astype(float).astype(int)
    grouped = frame.groupby("yr").sum(numeric_only=True)

    def col(name: str) -> float:
        return float(grouped[name].sum()) if name in grouped.columns else float("nan")

    diagnoses = {int(y): v for y, v in annual.get("new_diagnoses", {}).items()}
    msm = {int(y): v for y, v in annual.get("new_diagnoses_msm", {}).items()}
    non_msm_96_24 = sum(diagnoses.get(y, 0.0) - msm.get(y, 0.0) for y in range(1996, 2025))

    return {
        "new_diagnoses_1975_1995": total("new_diagnoses", 1975, 1995),
        "new_diagnoses_1996_2024": total("new_diagnoses", 1996, 2024),
        "new_diagnoses_msm_1996_2024": total("new_diagnoses_msm", 1996, 2024),
        "new_diagnoses_non_msm_1996_2024": float(non_msm_96_24),
        "new_infections_1975_2025": total("new_infections", 1975, 2025),
        "aids_deaths_1975_2025": total("aids_deaths", 1975, 2025),
        "PLHIV_2024": at("PLHIV", 2024),
        "on_art_stock_2024": at("on_art_stock", 2024),
        "plhiv_female_15plus_2024": at("plhiv_female_15plus", 2024),
        "new_infections_msm_total": col("new_infections_msm"),
        "new_infections_non_msm_total": col("new_infections_non_msm"),
    }


def capture(*, include_stochastic: bool = True) -> dict[str, Any]:
    """Run the model and capture the baseline. Does not write anything."""
    base = json.loads(CONFIG_PATH.read_text())

    result = _run(base, DETERMINISTIC_SEED, deterministic=True)
    annual = extract_annual_outputs(result, START_YEAR, END_YEAR)
    captured: dict[str, Any] = {
        "deterministic": {
            "seed": DETERMINISTIC_SEED,
            "digest": annual_digest(annual),
            "scalars": _scalars(annual, result["summary"]),
        },
        "code_fingerprints": {
            name: _sha256_file(ROOT / name) for name in TRACKED_MODULES
        },
        "config_sha256": _sha256_file(CONFIG_PATH),
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "metrics": list(METRICS),
    }

    if include_stochastic:
        per_seed = {}
        for seed in STOCHASTIC_SEEDS:
            res = _run(base, seed, deterministic=False)
            ann = extract_annual_outputs(res, START_YEAR, END_YEAR)
            per_seed[str(seed)] = {
                "digest": annual_digest(ann),
                "scalars": _scalars(ann, res["summary"]),
            }
        captured["stochastic"] = {"seeds": list(STOCHASTIC_SEEDS), "per_seed": per_seed}

    return captured


def write_baseline(reason: str, *, include_stochastic: bool = True,
                   path: Path | None = None) -> Path:
    """
    Freeze the current model output as the baseline.

    RUN ONLY DELIBERATELY. ``reason`` is required and is stamped into the artefact:
    a baseline move with no recorded reason is indistinguishable from a silent
    re-baseline to make a test pass, which is exactly what this file exists to
    prevent.
    """
    if not reason or not reason.strip():
        raise ValueError(
            "write_baseline() requires a reason. A baseline move with no recorded "
            "reason is how a regression gets laundered into a new normal."
        )
    path = path or BASELINE_PATH
    payload = {
        "generated_by": "tools/postchange_baseline.py",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": reason.strip(),
        "warning": (
            "REGRESSION DETECTOR, NOT A CORRECTNESS CERTIFICATE. These values record "
            "what the model produced on the stamped date. They are not validated as "
            "correct and several are known provisional. Do not quote them as results."
        ),
        "supersedes": (
            "Nothing. This is an ADDENDUM baseline. Pre-change references "
            "(A-15/A-16/A-9 artefacts, the 8d186b35... digest assertions in "
            "run_a21_*/run_a22_*) are deliberately left in place and failing."
        ),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "baseline": capture(include_stochastic=include_stochastic),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_baseline(path: Path | None = None) -> dict[str, Any]:
    path = path or BASELINE_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Create it deliberately with "
            "write_baseline('<why>'), never as a side effect of a failing test."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def compare(*, include_stochastic: bool = True, path: Path | None = None) -> dict:
    """Current model output vs the stored baseline. Reports, does not assert."""
    stored = load_baseline(path)["baseline"]
    current = capture(include_stochastic=include_stochastic)

    drift: dict[str, Any] = {"digest_changed": {}, "scalar_drift": {}, "code_changed": {}}

    if stored["deterministic"]["digest"] != current["deterministic"]["digest"]:
        drift["digest_changed"]["deterministic"] = {
            "baseline": stored["deterministic"]["digest"],
            "current": current["deterministic"]["digest"],
        }
    for key, was in stored["deterministic"]["scalars"].items():
        now = current["deterministic"]["scalars"].get(key)
        if now is None or not np.isclose(was, now, rtol=0, atol=0, equal_nan=True):
            drift["scalar_drift"][f"deterministic.{key}"] = {"baseline": was, "current": now}

    for name, was in stored["code_fingerprints"].items():
        now = current["code_fingerprints"].get(name)
        if was != now:
            drift["code_changed"][name] = {"baseline": was, "current": now}
    if stored["config_sha256"] != current["config_sha256"]:
        drift["code_changed"]["<config>"] = {
            "baseline": stored["config_sha256"], "current": current["config_sha256"]}

    if include_stochastic and "stochastic" in stored and "stochastic" in current:
        for seed, was in stored["stochastic"]["per_seed"].items():
            now = current["stochastic"]["per_seed"].get(seed)
            if now is None:
                drift["digest_changed"][f"stochastic.{seed}"] = {
                    "baseline": was["digest"], "current": None}
            elif was["digest"] != now["digest"]:
                drift["digest_changed"][f"stochastic.{seed}"] = {
                    "baseline": was["digest"], "current": now["digest"]}

    drift["clean"] = not (drift["digest_changed"] or drift["scalar_drift"]
                          or drift["code_changed"])
    return drift


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="freeze current output as the baseline")
    w.add_argument("--reason", required=True)
    w.add_argument("--no-stochastic", action="store_true")
    c = sub.add_parser("compare", help="report drift vs the stored baseline")
    c.add_argument("--no-stochastic", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "write":
        path = write_baseline(args.reason, include_stochastic=not args.no_stochastic)
        print(f"baseline written: {path}")
        stored = load_baseline()["baseline"]
        print(f"  deterministic digest: {stored['deterministic']['digest']}")
        return 0

    drift = compare(include_stochastic=not args.no_stochastic)
    if drift["clean"]:
        print("CLEAN — model output matches the stored baseline.")
        return 0
    print("DRIFT DETECTED\n")
    for section in ("code_changed", "digest_changed", "scalar_drift"):
        if drift[section]:
            print(f"  {section}:")
            for key, val in drift[section].items():
                print(f"    {key}: {val['baseline']} -> {val['current']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
