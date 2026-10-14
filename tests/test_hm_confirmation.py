from __future__ import annotations

import json

import pandas as pd
import pytest

import run_hm_confirmation as confirm


def test_aggregate_replicates_preserves_source_ids_parameters_and_sample_variance():
    selected = pd.DataFrame({"design_id": [25, 342], "a": [1.0, 2.0]})
    raw = pd.DataFrame({
        "design_id": [0, 1, 2, 3],
        "source_design_id": [25, 25, 342, 342],
        "confirmation_seed": [10, 11, 10, 11],
        "confirmation_task_id": [0, 1, 2, 3],
        "a": [1.0, 1.0, 2.0, 2.0],
        "new_diagnoses_2024": [10.0, 14.0, 20.0, 28.0],
        "mcvar__new_diagnoses_2024": [0.0, 0.0, 0.0, 0.0],
        "n_replicates": [1, 1, 1, 1],
        "replicate_seeds": ["10", "11", "10", "11"],
        "error": ["", "", "", ""],
    })
    result = confirm.aggregate_replicates(raw, selected, ["a"])
    assert result["design_id"].tolist() == [25, 342]
    assert result["a"].tolist() == [1.0, 2.0]
    assert result["new_diagnoses_2024"].tolist() == [12.0, 24.0]
    assert result["mcvar__new_diagnoses_2024"].tolist() == pytest.approx([8.0, 32.0])
    assert result["n_replicates"].tolist() == [2, 2]
    assert result["replicate_seeds"].tolist() == ["10;11", "10;11"]


def test_verified_manifest_rejects_corrupted_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    artifact = source / "data.csv"
    artifact.write_text("x\n1\n")
    manifest = {
        "artefacts": [{
            "path": "data.csv",
            "bytes": artifact.stat().st_size,
            "sha256": confirm._sha256(artifact),
        }]
    }
    (source / "manifest.json").write_text(json.dumps(manifest))
    assert confirm._verified_manifest(source)["artefacts"]
    artifact.write_text("changed")
    with pytest.raises(ValueError, match="integrity verification"):
        confirm._verified_manifest(source)
