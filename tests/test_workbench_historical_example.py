"""Integrity checks for the small, tracked workbench example; no simulations."""

from __future__ import annotations

from hashlib import sha256
import csv
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "workbench" / "historical_2008"


def test_historical_2008_example_matches_recorded_hashes():
    provenance = json.loads((EXAMPLE / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["classification"] == "HISTORICAL_RETAINED"
    assert provenance["current_code_compatibility"] == (
        "INCOMPATIBLE_WITH_CURRENT_OPTION_B_PRODUCTION_ROUTE"
    )
    for relative, expected in provenance["files"].items():
        path = EXAMPLE / relative
        assert path.is_file()
        assert sha256(path.read_bytes()).hexdigest() == expected


def test_historical_2008_example_contains_selected_confirmed_candidate():
    candidate = json.loads((EXAMPLE / "candidate_1787.json").read_text(encoding="utf-8"))
    selected = json.loads(
        (EXAMPLE / "selected_model_break_2008.json").read_text(encoding="utf-8")
    )
    binding = selected["_calibration_binding"]
    assert candidate["candidate_id"] == binding["selected_candidate_id"] == 1787
    assert candidate["params_sha256"] == binding["params_sha256"]
    assert binding["confirmation_primary_pass"] is True
    assert len(candidate["replicate_seeds"]) == len(candidate["per_seed"]) == 20
    assert all("annual_outputs" in item for item in candidate["per_seed"])


def test_default_reference_has_lowest_confirmed_i_max_in_retained_shortlist():
    with (EXAMPLE / "confirmation_summary.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    selected = min(rows, key=lambda row: float(row["confirmation_primary_i_max"]))
    assert selected["candidate_id"] == "1787"
    assert float(selected["confirmation_primary_i_max"]) == pytest.approx(
        2.5385477355491948
    )


def test_recorded_target_means_equal_the_seed_level_annual_outputs():
    candidate = json.loads((EXAMPLE / "candidate_1787.json").read_text(encoding="utf-8"))
    metric_by_source = {
        "shm_new_diagnoses_msm": "new_diagnoses_msm",
        "rivm_aids_msm": "aids_first_diagnosis_proxy_msm",
    }
    for index, target in enumerate(candidate["target_order"]):
        source, raw_year = target.split("@", 1)
        metric = metric_by_source[source]
        values = [
            float(item["annual_outputs"][metric][raw_year])
            for item in candidate["per_seed"]
        ]
        assert sum(values) / len(values) == pytest.approx(candidate["means"][index])


def test_target_fit_extract_is_candidate_1787_and_matches_recorded_means():
    candidate = json.loads((EXAMPLE / "candidate_1787.json").read_text(encoding="utf-8"))
    with (EXAMPLE / "target_fit_candidate_1787.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert [row["target_key"] for row in rows] == candidate["target_order"]
    assert {row["candidate_id"] for row in rows} == {"1787"}
    assert [float(row["simulated_mean"]) for row in rows] == pytest.approx(
        candidate["means"]
    )
    assert all(row["primary_target_pass"] == "True" for row in rows)
