from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import pytest

import coupled_calibration_search as ccs
import hm_target_register as tr
import nroy_contract as nc
from tools import prepare_robust_nroy_acceptance_inputs as prep


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "configs" / "robust_nroy_freeze_production.json"
EXTERNAL_WORKBOOK = (ROOT / "../data/raw/validation/DiseaseData.xlsx").resolve()
EXTERNAL_TARGET_CANDIDATE = ROOT / (
    "outputs/calibration/rivm_aids_vintage_audit_20260916/"
    "candidate_newest_vetted_20260916.json"
)
EXTERNAL_INPUTS = nc.strict_json_load(
    ROOT / "data" / "external_inputs.json", what="external-input manifest"
)
EXTERNAL_INPUTS_BY_ID = {
    item["logical_id"]: item for item in EXTERNAL_INPUTS["inputs"]
}


def _is_exact_external_input(path: Path, logical_id: str) -> bool:
    expected = EXTERNAL_INPUTS_BY_ID[logical_id]["sha256"]
    return path.is_file() and ccs.sha256_file(path) == expected


EXTERNAL_ACCEPTANCE_INPUTS_PRESENT = (
    _is_exact_external_input(EXTERNAL_WORKBOOK, "history_matching_target_workbook")
    and _is_exact_external_input(
        EXTERNAL_TARGET_CANDIDATE, "rivm_aids_newest_vetted_target_candidate"
    )
)
EXTERNAL_ACCEPTANCE_INPUTS_REASON = (
    "external-data lane requires the exact manifest-hashed DiseaseData.xlsx and "
    "candidate_newest_vetted_20260916.json"
)


def args(out: Path, *, draft: bool) -> argparse.Namespace:
    return argparse.Namespace(
        plan=str(PLAN),
        wave_plan=str(prep.DEFAULT_WAVE_PLAN),
        out=str(out),
        draft=draft,
    )


@pytest.mark.skipif(
    not EXTERNAL_ACCEPTANCE_INPUTS_PRESENT,
    reason=EXTERNAL_ACCEPTANCE_INPUTS_REASON,
)
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


@pytest.mark.skipif(
    not EXTERNAL_ACCEPTANCE_INPUTS_PRESENT,
    reason=EXTERNAL_ACCEPTANCE_INPUTS_REASON,
)
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


def test_generator_rejects_a_structurally_valid_wrong_workbook(tmp_path):
    wrong_workbook = tmp_path / "wrong-workbook.xlsx"
    with pd.ExcelWriter(wrong_workbook) as writer:
        pd.DataFrame({"Year": [2024], "Value": [1]}).to_excel(
            writer, sheet_name="S", index=False
        )

    register = nc.strict_json_load(
        ROOT / "configs" / "hm_target_register_focused_20260916.json",
        what="target register fixture",
    )
    register["source_workbook"] = str(wrong_workbook)
    # Keep the production-bound expected digest, so a structurally parseable
    # substitute cannot become an acceptance source merely by being recorded.
    register_path = tmp_path / "target_register.json"
    register_path.write_text(json.dumps(register))

    plan = nc.strict_json_load(PLAN, what="production plan fixture")
    plan["target_contract"]["register_path_proposal"] = str(register_path)
    plan["target_contract"]["register_sha256_proposal"] = ccs.sha256_file(register_path)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    generate_args = args(tmp_path / "out", draft=True)
    generate_args.plan = str(plan_path)

    with pytest.raises(tr.RegisterError, match="does not match the register-bound"):
        prep.generate(generate_args)
