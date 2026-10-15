#!/usr/bin/env python3
"""Independent read-only recomputation of a coupled calibration search.

Runs no simulation, imports no part of the search harness and calls none of its
report functions.  Every number is recomputed here from the raw per-seed records
and the frozen design, then compared with what the experiment wrote.  A
disagreement is an error, never a rounding note.

    tools/review_coupled_calibration.py --source <search dir> \
        [--confirmation <confirmation dir>] --output-dir <new dir>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

TOLERANCE = 1e-9


class ReviewError(RuntimeError):
    pass


def _strict_object_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReviewError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise ReviewError(f"non-finite JSON token {value!r} is forbidden")


def _parse_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ReviewError(f"JSON number {value!r} overflows to a non-finite value")
    return parsed


def strict_json_loads(text: str, *, what: str) -> object:
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_constant,
            parse_float=_parse_float,
        )
    except json.JSONDecodeError as exc:
        raise ReviewError(f"{what} is not valid strict JSON: {exc}") from exc


def strict_json(path: Path, *, what: str | None = None) -> object:
    path = Path(path)
    return strict_json_loads(
        path.read_text(encoding="utf-8"), what=what or f"JSON file {path}"
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv_rows(path: Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    out = []
    for line_number, line in enumerate(
            Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            row = strict_json_loads(line, what=f"{path}:{line_number}")
            if not isinstance(row, dict):
                raise ReviewError(f"{path}:{line_number} must be a JSON object")
            out.append(row)
    return out


def as_bool(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def verify_artifacts(directory: Path) -> dict:
    index = strict_json(directory / "artifacts.json", what="artifact index")
    checked = 0
    for entry in index["files"]:
        path = directory / entry["path"]
        if not path.is_file():
            raise ReviewError(f"{directory}: artifact missing: {entry['path']}")
        if path.stat().st_size != entry["bytes"]:
            raise ReviewError(f"{directory}: size mismatch: {entry['path']}")
        if sha256(path) != entry["sha256"]:
            raise ReviewError(f"{directory}: sha256 mismatch: {entry['path']}")
        if path.suffix == ".json":
            strict_json(path)
        elif path.suffix == ".jsonl":
            read_jsonl(path)
        checked += 1
    return {"files_verified": checked}


# ==========================================================================
# Independent arithmetic
# ==========================================================================

def load_frozen_targets(directory: Path, expected_n: int) -> list[dict]:
    rows = read_csv_rows(directory / "frozen_targets.csv")
    targets = []
    for row in rows:
        v_obs = float(row["v_obs_variance"])
        v_code = float(row["v_code_variance"])
        v_disc = float(row["v_disc_variance"])
        extra = float(row["extra_variance"])
        recomputed = v_obs + v_code + v_disc + extra
        if not math.isclose(recomputed, float(row["b_variance"]), rel_tol=1e-12, abs_tol=0.0):
            raise ReviewError(f"{row['key']}: recorded B does not equal the sum of its terms")
        if not math.isfinite(recomputed) or recomputed <= 0.0:
            raise ReviewError(f"{row['key']}: non-positive or non-finite B")
        targets.append({"key": row["key"], "z": float(row["z"]), "b": recomputed,
                        "v_obs": v_obs, "v_code": v_code, "v_disc": v_disc, "extra": extra,
                        "group": row["group"]})
    if len(targets) != expected_n:
        raise ReviewError(f"expected {expected_n} frozen targets, found {len(targets)}")
    if len({t['key'] for t in targets}) != expected_n:
        raise ReviewError("frozen targets are not distinct")
    return targets


def group_order(targets: list[dict]) -> list[str]:
    seen: list[str] = []
    for target in targets:
        if target["group"] not in seen:
            seen.append(target["group"])
    return seen


def recompute(matrix: np.ndarray, targets: list[dict], groups: list[str], *,
              replicates: int, discrepancy: float, cutoff: float) -> dict:
    """Means, ddof=1 variances, signed residuals, R_G, J and implausibilities."""
    z = np.array([t["z"] for t in targets], dtype=float)
    b = np.array([t["b"] for t in targets], dtype=float)
    disc = np.array([t["v_disc"] for t in targets], dtype=float)
    means = matrix.mean(axis=0)
    n = matrix.shape[0]
    variances = matrix.var(axis=0, ddof=1)
    residuals = (means - z) / np.sqrt(b)
    values = {}
    for group in groups:
        idx = [i for i, t in enumerate(targets) if t["group"] == group]
        values[group] = float(np.sqrt(np.mean(residuals[idx] ** 2)))
    j = float(max(values[g] for g in groups))
    mean_r = float(np.mean([values[g] for g in groups]))
    scale = (discrepancy / 0.1) ** 2
    b_scaled = b - disc + disc * scale
    total = b_scaled + variances * (1.0 + 1.0 / replicates)
    implausibility = np.abs(means - z) / np.sqrt(total)
    return {"n_replicates": n, "means": means, "variances": variances, "residuals": residuals,
            "group_values": values, "j": j, "mean_r": mean_r,
            "implausibility": implausibility, "i_max": float(implausibility.max()),
            "binding": targets[int(np.argmax(implausibility))]["key"],
            "passed": bool(np.all(implausibility <= cutoff))}


def load_raw(directory: Path, targets: list[dict]) -> dict[int, dict]:
    records = {}
    for path in sorted((directory / "raw").glob("candidate_*.json")):
        raw = strict_json(path, what=f"raw record {path.name}")
        if raw["target_order"] != [t["key"] for t in targets]:
            raise ReviewError(f"{path.name}: target order differs from the frozen order")
        matrix = np.array(raw["target_matrix"], dtype=float)
        if not np.isfinite(matrix).all():
            raise ReviewError(f"{path.name}: non-finite raw replicate output")
        seeds = [int(s) for s in raw["replicate_seeds"]]
        if len(seeds) != len(set(seeds)) or matrix.shape[0] != len(seeds):
            raise ReviewError(f"{path.name}: incomplete or duplicated replicate seed matrix")
        if len(raw["per_seed"]) != len(seeds):
            raise ReviewError(f"{path.name}: per-seed records do not match the seed matrix")
        records[int(raw["candidate_id"])] = {"raw": raw, "matrix": matrix, "seeds": seeds,
                                             "file": path.name}
    if not records:
        raise ReviewError(f"{directory}: no raw candidate records")
    return records


def check_budget(directory: Path, manifest: dict, replicates: int) -> dict:
    dispatch = read_jsonl(directory / "journal/dispatch.jsonl")
    results = read_jsonl(directory / "journal/results.jsonl")
    ids = [int(r["candidate_id"]) for r in dispatch]
    if len(ids) != len(set(ids)):
        raise ReviewError("a candidate was dispatched more than once")
    launched = sum(int(r["trajectories"]) for r in dispatch)
    if any(int(r["trajectories"]) != replicates for r in dispatch):
        raise ReviewError("a dispatch record does not carry the declared replicate count")
    budget = manifest["budget"]
    if launched != int(budget["launched"]):
        raise ReviewError(f"budget disagreement: journal {launched}, manifest {budget['launched']}")
    if int(budget["launched"]) > int(budget["cap"]):
        raise ReviewError("the launched trajectory count exceeds the declared cap")
    successes = [r for r in results if not r.get("error")]
    lost = len(ids) - len({int(r['candidate_id']) for r in successes})
    if int(budget["lost_in_flight"]) != lost * replicates:
        raise ReviewError("lost-in-flight trajectories are not accounted for")
    return {"dispatched_candidates": len(ids), "trajectories_launched": launched,
            "cap": int(budget["cap"]), "lost_candidates": lost,
            "journal_results": len(results)}


def check_shortlist(directory: Path, computed: dict, cutoff: float) -> dict:
    shortlist = strict_json(directory / "shortlist.json", what="search shortlist")
    entries = shortlist["entries"]
    ranked = sorted(computed, key=lambda cid: (computed[cid]["j"], computed[cid]["mean_r"], cid))
    expected: list[tuple[int, str]] = []
    for cid in ranked:
        if len(expected) >= 5:
            break
        if computed[cid]["passed"]:
            expected.append((cid, "discovery_primary_pass"))
    chosen = {cid for cid, _ in expected}
    for cid in ranked:
        if len(expected) >= 10:
            break
        if cid in chosen:
            continue
        expected.append((cid, "lowest_J_additional_primary_pass" if computed[cid]["passed"]
                         else "lowest_J_near_miss"))
        chosen.add(cid)
    if [(int(e["candidate_id"]), e["selected_as"]) for e in entries] != expected:
        raise ReviewError(f"recomputed shortlist differs: expected {expected}, "
                          f"found {[(e['candidate_id'], e['selected_as']) for e in entries]}")
    return {"size": len(entries), "entries": [e["candidate_id"] for e in entries],
            "n_primary_pass_heads": sum(1 for _, role in expected
                                        if role == "discovery_primary_pass")}


# ==========================================================================
# Review
# ==========================================================================

def review_stage(directory: Path, targets: list[dict], groups: list[str], *, replicates: int,
                 cutoff: float, summary_file: str, i_max_column: str, pass_column: str,
                 j_column: str | None) -> tuple[dict, dict]:
    records = load_raw(directory, targets)
    summary = {int(row["candidate_id"]): row for row in read_csv_rows(directory / summary_file)}
    if set(summary) != set(records):
        raise ReviewError(f"{directory/summary_file}: candidate set differs from the raw records")
    computed: dict[int, dict] = {}
    for cid, record in records.items():
        if len(record["seeds"]) != replicates:
            raise ReviewError(f"candidate {cid}: {len(record['seeds'])} replicates, "
                              f"expected {replicates}")
        primary = recompute(record["matrix"], targets, groups, replicates=replicates,
                            discrepancy=0.1, cutoff=cutoff)
        sensitivity = recompute(record["matrix"], targets, groups, replicates=replicates,
                                discrepancy=0.2, cutoff=cutoff)
        stored = summary[cid]
        if abs(primary["i_max"] - float(stored[i_max_column])) > TOLERANCE:
            raise ReviewError(f"candidate {cid}: I_max {primary['i_max']} vs stored "
                              f"{stored[i_max_column]}")
        if primary["passed"] != as_bool(stored[pass_column]):
            raise ReviewError(f"candidate {cid}: acceptance decision differs")
        if j_column and abs(primary["j"] - float(stored[j_column])) > TOLERANCE:
            raise ReviewError(f"candidate {cid}: J {primary['j']} vs stored {stored[j_column]}")
        for group, value in primary["group_values"].items():
            column = f"R_{group}"
            if column in stored and abs(value - float(stored[column])) > TOLERANCE:
                raise ReviewError(f"candidate {cid}: R_{group} differs")
        np.testing.assert_allclose(primary["means"],
                                   np.array(record["raw"]["means"], dtype=float), atol=1e-12)
        np.testing.assert_allclose(primary["variances"],
                                   np.array(record["raw"]["sample_variances"], dtype=float),
                                   atol=1e-12)
        computed[cid] = {**primary, "sensitivity_passed": sensitivity["passed"],
                         "sensitivity_i_max": sensitivity["i_max"], "seeds": record["seeds"]}
    per_target = read_csv_rows(directory / "per_target_implausibility.csv") \
        if (directory / "per_target_implausibility.csv").exists() else []
    for row in per_target:
        cid = int(row["candidate_id"])
        index = [t["key"] for t in targets].index(row["target_key"])
        if abs(computed[cid]["implausibility"][index] - float(row["primary_implausibility"])) > TOLERANCE:
            raise ReviewError(f"candidate {cid}: per-target implausibility differs at "
                              f"{row['target_key']}")
    return records, computed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--confirmation", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ReviewError("the review output directory must be empty")

    manifest = strict_json(args.source / "manifest.json", what="search manifest")
    if manifest["status"] != "SEARCH_COMPLETE":
        raise ReviewError(f"only a SEARCH_COMPLETE run can be reviewed; found {manifest['status']}")
    context = strict_json(args.source / "search_context.json", what="search context")
    plan = strict_json(
        Path(context["prepared_dir"]) / "frozen_plan.json", what="frozen plan"
    )
    cutoff = float(plan["acceptance"]["cutoff"])
    discovery_seeds = [int(s) for s in plan["seeds"]["discovery"]]
    confirmation_seeds = [int(s) for s in plan["seeds"]["confirmation"]]
    if set(discovery_seeds) & set(confirmation_seeds):
        raise ReviewError("the declared discovery and confirmation seeds overlap")

    report: dict = {
        "status": "VERIFIED", "source": str(args.source),
        "source_manifest_sha256": sha256(args.source / "manifest.json"),
        "review_script_sha256": sha256(Path(__file__)),
        "artifacts": {"search": verify_artifacts(args.source)},
        "plan_sha256": sha256(Path(context["prepared_dir"]) / "frozen_plan.json"),
        "cutoff": cutoff,
    }
    targets = load_frozen_targets(args.source, int(plan["targets"]["n"]))
    groups = group_order(targets)
    records, computed = review_stage(
        args.source, targets, groups, replicates=len(discovery_seeds), cutoff=cutoff,
        summary_file="archive_summary.csv", i_max_column="discovery_primary_i_max",
        pass_column="discovery_primary_pass", j_column="search_j")
    for cid, record in records.items():
        if record["seeds"] != discovery_seeds:
            raise ReviewError(f"candidate {cid} did not use the declared discovery seed matrix")
    report["budget"] = check_budget(args.source, manifest, len(discovery_seeds))
    report["shortlist"] = check_shortlist(args.source, computed, cutoff)
    ranked = sorted(computed, key=lambda cid: (computed[cid]["j"], computed[cid]["mean_r"], cid))
    report["search"] = {
        "n_candidates": len(computed),
        "n_primary_pass": sum(1 for v in computed.values() if v["passed"]),
        "n_sensitivity_only": sum(1 for v in computed.values()
                                  if v["sensitivity_passed"] and not v["passed"]),
        "best_j": computed[ranked[0]]["j"], "best_candidate": ranked[0],
        "best_group_values": computed[ranked[0]]["group_values"],
    }
    if report["search"]["n_primary_pass"] != manifest["n_discovery_primary_pass"]:
        raise ReviewError("the recomputed number of primary passes differs from the manifest")

    confirmation_report = None
    if args.confirmation:
        cm = strict_json(args.confirmation / "manifest.json", what="confirmation manifest")
        if cm["status"] != "CONFIRMATION_COMPLETE":
            raise ReviewError(f"confirmation status is {cm['status']}")
        report["artifacts"]["confirmation"] = verify_artifacts(args.confirmation)
        if strict_json(
                args.confirmation / "frozen_shortlist.json", what="confirmed shortlist"
        ) != strict_json(args.source / "shortlist.json", what="search shortlist"):
            raise ReviewError("the confirmed shortlist is not the one the search froze")
        c_targets = load_frozen_targets(args.source, int(plan["targets"]["n"]))
        c_records, c_computed = review_stage(
            args.confirmation, c_targets, groups, replicates=len(confirmation_seeds),
            cutoff=cutoff, summary_file="confirmation_summary.csv",
            i_max_column="confirmation_primary_i_max", pass_column="confirmation_primary_pass",
            j_column=None)
        for cid, record in c_records.items():
            if record["seeds"] != confirmation_seeds:
                raise ReviewError(f"candidate {cid} did not use the fresh confirmation seeds")
            if set(record["seeds"]) & set(discovery_seeds):
                raise ReviewError("confirmation reused a discovery seed")
        check_budget(args.confirmation, cm, len(confirmation_seeds))
        confirmation_report = {
            "n_candidates": len(c_computed),
            "n_primary_pass": sum(1 for v in c_computed.values() if v["passed"]),
            "recomputed": {cid: {"i_max": v["i_max"], "binding": v["binding"],
                                 "passed": v["passed"], "j": v["j"]}
                           for cid, v in sorted(c_computed.items())},
        }
        if confirmation_report["n_primary_pass"] != cm["n_confirmation_primary_pass"]:
            raise ReviewError("the recomputed confirmation passes differ from the manifest")
        report["confirmation"] = confirmation_report

    args.output_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Verified coupled calibration search", "",
        "Every figure below was recomputed from the raw per-seed records by this script. It "
        "imports no part of the search harness and calls none of its report functions. All "
        "artifact hashes, seed matrices and budget counts were verified.", "",
        f"- Candidates evaluated: **{report['search']['n_candidates']}**",
        f"- Trajectories launched: **{report['budget']['trajectories_launched']}** of "
        f"{report['budget']['cap']} (lost in flight: {report['budget']['lost_candidates']} "
        f"candidates)",
        f"- Discovery primary passes: **{report['search']['n_primary_pass']}**; "
        f"sensitivity-only: {report['search']['n_sensitivity_only']}",
        f"- Best search J: **{report['search']['best_j']:.4f}** "
        f"(candidate {report['search']['best_candidate']})", "",
        "## Best ten by recomputed search rank", "",
        "| Rank | Candidate | J | mean R_G | Primary I_max | Binding | Primary pass |",
        "|---:|---:|---:|---:|---:|---|---|",
    ]
    for rank, cid in enumerate(ranked[:10], start=1):
        v = computed[cid]
        lines.append(f"| {rank} | {cid} | {v['j']:.4f} | {v['mean_r']:.4f} | {v['i_max']:.4f} | "
                     f"{v['binding']} | {'yes' if v['passed'] else 'no'} |")
    if confirmation_report:
        lines += ["", "## Recomputed confirmation", "",
                  "| Candidate | Confirmation I_max | Binding | Primary pass |",
                  "|---:|---:|---|---|"]
        for cid, v in confirmation_report["recomputed"].items():
            lines.append(f"| {cid} | {v['i_max']:.4f} | {v['binding']} | "
                         f"{'yes' if v['passed'] else 'no'} |")
    lines += ["", "## What this verification does and does not establish", "",
              "It establishes that the recorded numbers follow from the recorded raw replicates "
              "under the frozen design, that no candidate was dispatched twice, that the "
              "trajectory budget was respected and that the shortlist follows its declared rule.",
              "",
              "It does NOT establish that the model is correct, that the AIDS observation proxy "
              "is valid, that the search covered the 33-dimensional space, or that any candidate "
              "is a calibrated ensemble member or an HTA baseline. A low search J is not "
              "acceptance."]
    (args.output_dir / "VERIFIED_RESULTS.md").write_text("\n".join(lines) + "\n")
    (args.output_dir / "verification.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
