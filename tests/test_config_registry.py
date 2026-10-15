"""Static completeness and safety checks for the configuration inventory."""

from __future__ import annotations

import csv

from tools import build_config_registry as registry


def test_tracked_config_registry_is_complete_and_current():
    assert registry.inventory_problems() == []
    assert registry.check_registry() == []

    rows = registry.registry_rows()
    with registry.REGISTRY_PATH.open(newline="", encoding="utf-8") as stream:
        tracked = list(csv.DictReader(stream))
    assert tracked == rows
    assert len(rows) == 79
    assert len({row["path"] for row in rows}) == len(rows)
    assert {row["role_class"] for row in rows} <= registry.ALLOWED_ROLES


def test_registry_is_not_a_scientific_promotion_mechanism():
    rows = registry.registry_rows()
    assert not any("PRODUCTION_APPROVED" in row["scientific_status"] for row in rows)
    assert all(row["safe_collaborator_use"].strip() for row in rows)
    assert all(row["sha256"] and len(row["sha256"]) == 64 for row in rows)

    current = [row for row in rows if row["role_class"] == registry.ROLE_CURRENT]
    assert len(current) == 1
    status = current[0]["scientific_status"]
    note = current[0]["safe_collaborator_use"]
    assert "REQUIRES_RECALIBRATION" in status
    assert "not fitted" in status and "not production eligible" in status
    assert "newly authorized calibration" in note
    assert "Do not report its inherited values as a 1980 fit" in note

    research_path = (
        "configs/coupled_search_plan_option_b_1980_research_e2e_20261015.json"
    )
    research_plan = next(row for row in rows if row["path"] == research_path)
    assert research_plan["role_class"] == registry.ROLE_SUITE
    assert "RESEARCH_E2E_ONLY" in research_plan["scientific_status"]
    assert "cannot authorize production" in research_plan["safe_collaborator_use"]


def test_registry_checker_reports_an_unclassified_file(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "unexpected.json").write_text("{}")
    problems = registry.inventory_problems(config_dir)
    assert any("lack registry classification" in problem for problem in problems)
    assert any("unexpected.json" in problem for problem in problems)
