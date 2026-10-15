"""Static contract for the source-only guarded test environment."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_REQUIREMENTS = ROOT / "requirements-test.txt"
TESTING_GUIDE = ROOT / "documentation" / "guides" / "TESTING.md"


def _requirement_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_test_environment_is_an_exact_pinned_operator_superset():
    assert _requirement_lines(TEST_REQUIREMENTS) == [
        "-r requirements-operator.txt",
        "matplotlib==3.11.1",
        "openpyxl==3.1.5",
        "tabulate==0.10.0",
        "pytest==9.1.1",
    ]


def test_fresh_clone_guarded_gate_is_documented_exactly():
    guide = TESTING_GUIDE.read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    for document in (guide, readme):
        assert "python3.11 -m venv .venv-test" in document
        assert "pip install -r requirements-test.txt" in document
        assert "-p pytest_no_engine_guard -W error -q" in document
    assert ".venv-test/bin/python tools/build_config_registry.py --check" in guide
    assert ".venv-test/" in gitignore.splitlines()
