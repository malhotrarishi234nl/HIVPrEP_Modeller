#!/usr/bin/env python3
"""Prepare or inspect the conference overnight suite without simulation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / "configs/suites/conference_overnight_20260929.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def _case_commands(suite: str, case_id: str, workers: int) -> str:
    case = f"{suite}/cases/{case_id}"
    plan = f"{case}/resolved_configs/coupled_search_plan.json"
    calibration = f"{case}/calibration"
    return f"""  .venv/bin/python run_coupled_calibration.py --validate-only --plan {plan}
  .venv/bin/python run_coupled_calibration.py --prepare --plan {plan} --output-dir {calibration}/prepared
  .venv/bin/python run_coupled_calibration.py --smoke --prepared {calibration}/prepared --output-dir {calibration}/smoke --workers {workers}
  .venv/bin/python run_coupled_calibration.py --run-search --prepared {calibration}/prepared --smoke-source {calibration}/smoke --output-dir {calibration}/search --workers {workers}
  .venv/bin/python run_coupled_calibration.py --confirm --source {calibration}/search --output-dir {calibration}/confirmation --workers {workers}
"""


def _calibration_script(spec: dict, output_rel: str) -> str:
    suite = spec["calibration_suite"]
    workers = int(spec["calibration"].get("fallback_workers_per_case", 8))
    cases = spec["calibration"]["cases"]
    functions = []
    launches = []
    for case in cases:
        functions.append(f"run_{case}() {{\n{_case_commands(suite, case, workers)}}}\n")
        launches.append(f"run_{case} > \"$LOG_DIR/{case}.log\" 2>&1 &\npids+=(\"$!\")")
    return """#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
LOG_DIR={output_rel}/logs
mkdir -p "$LOG_DIR"

""" + "\n".join(functions) + "\n" + "\n".join([
        "pids=()", *launches,
        "status=0",
        "for pid in \"${pids[@]}\"; do wait \"$pid\" || status=1; done",
        "exit \"$status\"",
        "",
    ])


def _post_calibration_script(spec: dict, output_rel: str) -> str:
    suite = spec["calibration_suite"]
    policy = spec["policy_preparation"]
    draws = int(spec["psa"]["draws"])
    seed = int(spec["psa"]["seed"])
    p_workers = int(spec["psa"]["primary_workers_per_shard"])
    return f"""#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
OUT={output_rel}
STATUS_FILE="$OUT/SUITE_STATUS.txt"
printf 'RUNNING %s\\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATUS_FILE"
trap 'code=$?; if [ "$code" -ne 0 ]; then printf "FAILED exit=%s %s\\n" "$code" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATUS_FILE"; fi' EXIT
PSA_TOTAL_WORKERS=$(.venv/bin/python -c 'import json; print(json.load(open("{output_rel}/hpc_scaling_benchmark.json"))["recommended_psa_total_workers"])')
PSA_WORKERS_PER_SHARD={p_workers}
PSA_SHARDS=$((PSA_TOTAL_WORKERS / PSA_WORKERS_PER_SHARD))
if [ "$PSA_SHARDS" -lt 1 ]; then echo "Invalid PSA shard count" >&2; exit 2; fi
echo "Using $PSA_SHARDS PSA shards x $PSA_WORKERS_PER_SHARD workers = $((PSA_SHARDS * PSA_WORKERS_PER_SHARD)) workers"

.venv/bin/python compare_coupled_calibration_suite.py --suite-root {suite} --output-dir "$OUT/calibration_comparison"
.venv/bin/python tools/bind_calibration_suite.py --suite-root {suite} --output-dir "$OUT/bound_models"

.venv/bin/python run_policy_matrix.py --prepare --model-config "$OUT/bound_models/selected_model_break_2008.json" --hta-config {policy}/hta_runtime_primary_2008.json --output-dir "$OUT/policy_primary_2008" --seeds {spec['policy']['seeds']}
.venv/bin/python run_policy_matrix.py --prepare --model-config "$OUT/bound_models/selected_model_break_2010.json" --hta-config {policy}/hta_runtime_bridge_2010.json --output-dir "$OUT/policy_bridge_2010" --seeds {spec['policy']['seeds']}
.venv/bin/python run_policy_matrix.py --run --plan "$OUT/policy_primary_2008/plan.json" --workers {spec['policy']['primary_workers']} &
policy_primary_pid=$!
.venv/bin/python run_policy_matrix.py --run --plan "$OUT/policy_bridge_2010/plan.json" --workers {spec['policy']['bridge_workers']} &
policy_bridge_pid=$!
wait "$policy_primary_pid"
wait "$policy_bridge_pid"
.venv/bin/python run_policy_matrix.py --finalize --plan "$OUT/policy_primary_2008/plan.json"
.venv/bin/python run_policy_matrix.py --finalize --plan "$OUT/policy_bridge_2010/plan.json"

.venv/bin/python run_psa_hta_matrix.py --prepare --model-config "$OUT/bound_models/selected_model_break_2008.json" --hta-config {policy}/hta_runtime_primary_2008.json --psa-config {spec['psa_config']} --output-dir "$OUT/psa_primary_2008" --n-draws {draws} --seed {seed} --shard-count "$PSA_SHARDS"
seq 0 $((PSA_SHARDS - 1)) | xargs -P "$PSA_SHARDS" -I SHARD .venv/bin/python run_psa_hta_matrix.py --run-shard --plan "$OUT/psa_primary_2008/plan.json" --shard-index SHARD --workers "$PSA_WORKERS_PER_SHARD"
.venv/bin/python run_psa_hta_matrix.py --finalize --plan "$OUT/psa_primary_2008/plan.json"

.venv/bin/python run_psa_hta_matrix.py --prepare --model-config "$OUT/bound_models/selected_model_break_2010.json" --hta-config {policy}/hta_runtime_bridge_2010.json --psa-config {spec['psa_config']} --output-dir "$OUT/psa_bridge_2010" --n-draws {draws} --seed {seed} --shard-count "$PSA_SHARDS"
seq 0 $((PSA_SHARDS - 1)) | xargs -P "$PSA_SHARDS" -I SHARD .venv/bin/python run_psa_hta_matrix.py --run-shard --plan "$OUT/psa_bridge_2010/plan.json" --shard-index SHARD --workers "$PSA_WORKERS_PER_SHARD"
.venv/bin/python run_psa_hta_matrix.py --finalize --plan "$OUT/psa_bridge_2010/plan.json"
.venv/bin/python tools/finalize_conference_suite.py --suite-root "$OUT"
printf 'COMPLETE %s\\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATUS_FILE"
trap - EXIT
"""


def _h4d_calibration_script(spec: dict, output_rel: str) -> str:
    suite = spec["calibration_suite"]
    out = output_rel
    cases = spec["calibration"]["cases"]
    if cases != ["break_2008", "break_2010"]:
        raise ValueError("H4D launcher expects the reviewed 2008/2010 case order")
    lines = [
        "#!/usr/bin/env bash", "set -euo pipefail",
        'cd "$(dirname "$0")/../../.."',
        f"OUT={out}",
        'LOG_DIR="$OUT/logs"', 'mkdir -p "$LOG_DIR"', "",
    ]
    for case in cases:
        root = f"{suite}/cases/{case}"
        plan = f"{root}/resolved_configs/coupled_search_plan.json"
        calibration = f"{root}/calibration"
        lines.extend([
            f".venv/bin/python run_coupled_calibration.py --validate-only --plan {plan}",
            f".venv/bin/python run_coupled_calibration.py --prepare --plan {plan} "
            f"--output-dir {calibration}/prepared",
        ])
    benchmark_counts = ",".join(
        str(value) for value in spec["calibration"]["hpc_worker_counts_benchmarked"]
    )
    tasks = int(spec["calibration"]["hpc_benchmark_tasks_per_point"])
    lines.extend([
        "",
        ".venv/bin/python tools/benchmark_hpc_scaling.py "
        f"--prepared {suite}/cases/break_2008/calibration/prepared "
        '--output "$OUT/hpc_scaling_benchmark.json" '
        f"--worker-counts {benchmark_counts} --tasks-per-point {tasks}",
        "HPC_CASE_WORKERS=$(.venv/bin/python -c 'import json; "
        f"print(json.load(open(\"{out}/hpc_scaling_benchmark.json\"))"
        "[\"recommended_workers_per_parallel_calibration\"])')",
        'echo "Using $HPC_CASE_WORKERS workers per calibration case"', "",
    ])
    for case in cases:
        root = f"{suite}/cases/{case}"
        calibration = f"{root}/calibration"
        lines.extend([
            f"run_{case}() {{",
            f"  .venv/bin/python run_coupled_calibration.py --smoke --prepared "
            f"{calibration}/prepared --output-dir {calibration}/smoke "
            '--workers "$HPC_CASE_WORKERS" --allow-hpc-workers',
            f"  .venv/bin/python run_coupled_calibration.py --run-search --prepared "
            f"{calibration}/prepared --smoke-source {calibration}/smoke "
            f"--output-dir {calibration}/search --workers \"$HPC_CASE_WORKERS\" "
            "--allow-hpc-workers",
            f"  .venv/bin/python run_coupled_calibration.py --confirm --source "
            f"{calibration}/search --output-dir {calibration}/confirmation "
            '--workers "$HPC_CASE_WORKERS" --allow-hpc-workers',
            "}", "",
        ])
    lines.extend([
        "pids=()",
        'run_break_2008 > "$LOG_DIR/break_2008.log" 2>&1 &', 'pids+=("$!")',
        'run_break_2010 > "$LOG_DIR/break_2010.log" 2>&1 &', 'pids+=("$!")',
        "status=0", 'for pid in "${pids[@]}"; do wait "$pid" || status=1; done',
        'exit "$status"', "",
    ])
    return "\n".join(lines)


def prepare(spec_path: Path, output_dir: Path) -> dict:
    spec_path = spec_path.resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_rel = _rel(output_dir)
    referenced = {
        "calibration_suite": ROOT / spec["calibration_suite"],
        "policy_preparation": ROOT / spec["policy_preparation"],
        "psa_config": ROOT / spec["psa_config"],
    }
    missing = [str(path) for path in referenced.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"suite inputs are missing: {missing}")
    plan = {
        **spec,
        "status": "PREPARED_CALIBRATION_PENDING",
        "spec_file": _rel(spec_path),
        "spec_sha256": _sha256(spec_path),
        "input_sha256": {
            "psa_config": _sha256(referenced["psa_config"]),
            "policy_plan": _sha256(referenced["policy_preparation"] / "plan.json"),
            "calibration_suite_source": _sha256(
                referenced["calibration_suite"] / "suite_source.json"
            ),
        },
        "execution_layers": [
            "paired 2008/2010 coupled calibration",
            "15-seed stochastic CRN policy matrix",
            "1000-draw conditional LHS PSA matrix",
            "compact report binding (after simulation)",
        ],
        "simulations_started": 0,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_text(output_dir / "overnight_plan.json", json.dumps(plan, indent=2) + "\n")
    _atomic_text(output_dir / "01_run_calibrations.sh", _calibration_script(spec, output_rel))
    _atomic_text(
        output_dir / "01_h4d_benchmark_and_calibrations.sh",
        _h4d_calibration_script(spec, output_rel),
    )
    _atomic_text(output_dir / "02_run_downstream.sh", _post_calibration_script(spec, output_rel))
    os.chmod(output_dir / "01_run_calibrations.sh", 0o755)
    os.chmod(output_dir / "01_h4d_benchmark_and_calibrations.sh", 0o755)
    os.chmod(output_dir / "02_run_downstream.sh", 0o755)
    runbook = f"""# Conference overnight VM runbook

Prepared only; no simulations were launched.

## Machine

- x86-64 Linux, {spec['hardware']['vcpu']} vCPU
- at least {spec['hardware']['memory_gib_minimum']} GiB RAM
- {spec['hardware']['disk_gib']} GiB SSD
- on-demand instance for the first complete run

## Order

1. Copy the repository including the declared calibration source artifacts.
2. Run `./tools/setup_overnight_vm.sh` to create `.venv`, install the frozen
   simulation-only dependencies, run focused tests, and validate both plans.
3. On H4D, run `./{output_rel}/01_h4d_benchmark_and_calibrations.sh`.
   It benchmarks process counts. Each of the two simultaneous calibration cases
   is capped at 64 workers because an adaptive wave contains only 64 candidate
   tasks. The original eight-worker launcher is retained as a conservative fallback.
4. Review both confirmation manifests. The downstream binder refuses a non-passer.
5. Run `./{output_rel}/02_run_downstream.sh`.
6. Copy the complete prepared-suite directory back before terminating the VM.

The downstream launcher reuses the benchmark's best whole-machine worker count
for PSA, split into eight-worker shards. `SUITE_STATUS.txt` reads `COMPLETE` only
after all policy, PSA, and paired finalization checks finish; a shell failure
changes it to `FAILED`.

The final command writes paired 2008-minus-2010 policy and PSA tables under
`combined/`. The formatted professor workbook is generated from those checked
tables after the VM results return.

The policy and PSA runners are resumable at policy-seed and PSA scenario/draw
level. Re-running an individual runner/shard skips valid atomic result files.
The all-in-one downstream script is intended for its first clean launch; after
an interruption, resume the incomplete runner rather than restarting that shell
script from the beginning. Finalization never silently accepts partial output.
"""
    _atomic_text(output_dir / "VM_RUNBOOK.md", runbook)
    return plan


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.spec, args.output_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
