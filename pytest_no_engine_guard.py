"""Pytest plugin: skip any test that would construct a real SimulationEngineV3.

Usage (from the repository root):

    .venv/bin/python -m pytest -p pytest_no_engine_guard -rs <tests...>

Added 2026-09-22 for the focused stage-structured diagnosis calibration brief,
whose execution boundary lets an implementation agent run only synthetic or
mocked-engine tests. The patch is installed at configure time, so fixtures of
any scope are covered. ``pytest.skip`` raises a BaseException subclass, which
``pytest.raises(Exception)`` and ``except Exception`` do not swallow.
Tests that bypass the constructor (``object.__new__(SimulationEngineV3)``) are
mocked engines and still run. Every skip reason names the rule, so ``-rs``
lists exactly which tests must be run by the user.
"""

from __future__ import annotations

import pytest

REASON = "constructs a real SimulationEngineV3: user-run only (focused-calibration execution boundary)"


def pytest_configure(config):
    import run_v3  # noqa: PLC0415

    def refuse(self, *args, **kwargs):
        pytest.skip(REASON)

    run_v3.SimulationEngineV3.__init__ = refuse
