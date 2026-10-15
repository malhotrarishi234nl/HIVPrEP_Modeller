"""Static contract checks for the non-coder model-operator environment."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATOR_REQUIREMENTS = ROOT / "requirements-operator.txt"
OPERATOR_GUIDE = ROOT / (
    "documentation/reports/calibration/robust_nroy_readiness_2026-10-15/"
    "MODEL_OPERATOR_GUIDE.md"
)


def _requirement_lines() -> list[str]:
    return [
        line.strip()
        for line in OPERATOR_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_operator_requirements_are_the_exact_pinned_direct_runtime_contract():
    assert _requirement_lines() == [
        "numpy==2.4.6",
        "pandas==3.0.5",
        "scipy==1.17.1",
    ]


def test_operator_setup_and_no_external_workbook_boundary_are_documented():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    guide = OPERATOR_GUIDE.read_text(encoding="utf-8")

    for document in (readme, guide):
        assert "python3.11 -m venv .venv" in document
        assert "pip install -r requirements-operator.txt" in document
        assert "external validation workbooks" in document

    assert "VALID: M0 config route; stochastic=true; engine was not constructed" in guide
    assert "VALID: M1 config route; stochastic=true; engine was not constructed" in guide
    assert "Model Maintainer" in guide
    assert "run_reproducibility_manifest.json" in guide
