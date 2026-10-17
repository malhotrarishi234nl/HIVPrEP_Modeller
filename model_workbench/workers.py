"""Cross-platform worker selection and a bounded child-process probe."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import subprocess
import sys
from typing import Any, Callable


ALL_AVAILABLE_WORKERS = -1
MAX_CONFIGURED_WORKERS = 8


class WorkerConfigurationError(ValueError):
    """A requested local worker setting is not usable."""


def logical_cpu_count() -> int:
    """Return the logical CPU count with a safe fallback for restricted hosts."""

    return max(1, int(os.cpu_count() or 1))


def effective_worker_count(requested: int, work_units: int) -> int:
    """Resolve a fixed or all-available setting and cap it to useful work."""

    requested = int(requested)
    work_units = int(work_units)
    if work_units < 1:
        raise WorkerConfigurationError("work units must be positive")
    if requested == ALL_AVAILABLE_WORKERS:
        resolved = logical_cpu_count()
    elif 1 <= requested <= MAX_CONFIGURED_WORKERS:
        resolved = requested
    else:
        raise WorkerConfigurationError(
            f"workers must be 1 to {MAX_CONFIGURED_WORKERS}, or all available"
        )
    return max(1, min(resolved, work_units))


def worker_label(requested: int) -> str:
    if int(requested) == ALL_AVAILABLE_WORKERS:
        return f"All available ({logical_cpu_count()} detected)"
    count = int(requested)
    return f"{count} worker" if count == 1 else f"{count} workers"


def probe_parallel_processes(
    requested: int,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    max_probe_workers: int = 8,
) -> dict[str, Any]:
    """Check that independent Python child processes can launch concurrently.

    The probe never imports or runs the model.  It intentionally tests at most
    eight processes so selecting ``all available`` is harmless on large hosts.
    """

    available = logical_cpu_count()
    resolved = available if int(requested) == ALL_AVAILABLE_WORKERS else int(requested)
    tested = effective_worker_count(requested, max(1, max_probe_workers))
    script = (
        "import os,sys,time; "
        "time.sleep(0.05); "
        "print(str(os.getpid()) + ':' + sys.argv[1])"
    )

    def launch(index: int) -> subprocess.CompletedProcess[str]:
        return runner(
            [sys.executable, "-c", script, str(index)],
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=10,
        )

    results: list[subprocess.CompletedProcess[str]] = []
    with ThreadPoolExecutor(max_workers=tested, thread_name_prefix="workbench-probe") as pool:
        futures = [pool.submit(launch, index) for index in range(tested)]
        for future in as_completed(futures):
            results.append(future.result())
    failures = [result.returncode for result in results if result.returncode != 0]
    pids = sorted({
        result.stdout.strip().split(":", 1)[0]
        for result in results
        if result.returncode == 0 and result.stdout.strip()
    })
    return {
        "status": (
            "PASS"
            if not failures and len(results) == tested and len(pids) == tested
            else "FAIL"
        ),
        "requested": int(requested),
        "available": available,
        "tested": tested,
        "child_processes": len(pids),
        "limited_probe": resolved > tested,
        "failures": failures,
    }
