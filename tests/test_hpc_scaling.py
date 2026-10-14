from __future__ import annotations

import json
from pathlib import Path

import pytest

import run_coupled_calibration as calibration
from tools.benchmark_hpc_scaling import recommend
from tools.prepare_conference_overnight_suite import _post_calibration_script


ROOT = Path(__file__).resolve().parents[1]


def test_hpc_worker_override_is_explicit_and_hard_capped():
    args = calibration.parse_args([
        "--run-search", "--workers", "96", "--allow-hpc-workers"
    ])
    assert args.workers == 96
    assert args.allow_hpc_workers
    with pytest.raises(SystemExit, match="between 1 and 8"):
        calibration.main(["--workers", "9"])
    with pytest.raises(SystemExit, match="between 1 and 192"):
        calibration.main(["--workers", "193", "--allow-hpc-workers"])


def test_scaling_recommendation_uses_measured_throughput():
    result = recommend([
        {"status": "COMPLETE", "workers": 96, "trajectories_per_second": 10.0},
        {"status": "COMPLETE", "workers": 160, "trajectories_per_second": 14.0},
        {"status": "COMPLETE", "workers": 192, "trajectories_per_second": 13.5},
    ])
    assert result["recommended_total_workers"] == 160
    assert result["recommended_psa_total_workers"] == 160
    assert result["recommended_workers_per_parallel_calibration"] == 64


def test_h4d_downstream_uses_measured_psa_capacity_and_completion_marker():
    spec = json.loads(
        (ROOT / "configs/suites/conference_overnight_20260929.json").read_text()
    )
    script = _post_calibration_script(spec, "outputs/suites/example")
    assert '["recommended_psa_total_workers"]' in script
    assert 'PSA_SHARDS=$((PSA_TOTAL_WORKERS / PSA_WORKERS_PER_SHARD))' in script
    assert '--shard-count "$PSA_SHARDS"' in script
    assert "printf 'COMPLETE %s\\n'" in script
    assert 'printf "FAILED exit=%s %s\\n"' in script
