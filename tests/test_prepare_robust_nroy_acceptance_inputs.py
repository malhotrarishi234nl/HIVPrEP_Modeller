from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import coupled_calibration_search as ccs
import nroy_contract as nc
from tools import prepare_robust_nroy_acceptance_inputs as prep


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs" / "robust_nroy_freeze_production.json"
EXTERNAL_WORKBOOK = (ROOT / "../data/raw/validation/DiseaseData.xlsx").resolve()
EXTERNAL_WORKBOOK_REASON = (
    "external-data lane requires ../data/raw/validation/DiseaseData.xlsx"
)


def args(out: Path, *, draft: bool) -> argparse.Namespace:
    return argparse.Namespace(
        plan=str(PLAN),
        wave_plan=str(prep.DEFAULT_WAVE_PLAN),
        out=str(out),
        draft=draft,
    )


@pytest.mark.skipif(not EXTERNAL_WORKBOOK.is_file(), reason=EXTERNAL_WORKBOOK_REASON)
def test_unsigned_plan_can_only_generate_explicit_nonproduction_draft(tmp_path):
    out = tmp_path / "draft"
    assert prep.generate(args(out, draft=True)) == 0
    provenance = json.loads((out / "acceptance_input_provenance.json").read_text())
    observed = json.loads((out / "observed_values.json").read_text())
    base_variance = json.loads((out / "base_variance.json").read_text())

    assert provenance["status"] == "PRE_SIGN_REVIEW"
    assert provenance["production_eligible"] is False
    assert provenance["target_order"] == list(ccs.FROZEN_TARGET_ORDER)
    assert list(observed) == list(ccs.FROZEN_TARGET_ORDER)
    assert list(base_variance) == list(ccs.FROZEN_TARGET_ORDER)
    assert provenance["discrepancy"] == {
        "added_exactly_once": True,
        "fraction": 0.1,
        "method": "relative_to_target",
    }
    assert "excluded from B_i" in provenance["process_variance"]
    assert all(float(value) > 0.0 for value in base_variance.values())


def test_unsigned_plan_is_refused_for_final_evidence(tmp_path):
    with pytest.raises(nc.UnsignedConfigError, match="45 required value"):
        prep.generate(args(tmp_path / "final", draft=False))


@pytest.mark.skipif(not EXTERNAL_WORKBOOK.is_file(), reason=EXTERNAL_WORKBOOK_REASON)
def test_generator_refuses_to_overwrite_an_existing_package(tmp_path):
    out = tmp_path / "draft"
    assert prep.generate(args(out, draft=True)) == 0
    with pytest.raises(nc.ContractError, match="refusing to overwrite"):
        prep.generate(args(out, draft=True))


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ('{"schema": "first", "schema": "second"}', "duplicate JSON object key"),
        ('{"schema": NaN}', "non-finite JSON token"),
    ],
)
def test_draft_generator_uses_the_strict_plan_loader(tmp_path, text, problem):
    plan_path = tmp_path / "adversarial_plan.json"
    plan_path.write_text(text)
    generate_args = args(tmp_path / "out", draft=True)
    generate_args.plan = str(plan_path)
    with pytest.raises(nc.ContractError, match=problem):
        prep.generate(generate_args)
