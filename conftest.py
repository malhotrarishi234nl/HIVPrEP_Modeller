"""
pytest configuration — adds v11/ to sys.path so all test modules can import
production code (run_v3, scenario_comparator, etc.) without needing individual
sys.path.insert() calls in each file.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with -m 'not slow')")
