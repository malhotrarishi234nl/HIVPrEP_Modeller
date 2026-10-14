import copy
import json
from pathlib import Path

from config_utils import validate_tier_schema
from tools.prepare_conditional_psa_model import prepare


def test_conditional_psa_freezes_calibrated_entry_without_changing_value(tmp_path: Path):
    model = {
        "parameter": {
            "value": 0.8,
            "tier": "uncertain",
            "source": "test",
            "bounds": [0.7, 0.9],
            "distribution": {"type": "Beta", "alpha": 8, "beta": 2, "mean": 0.8},
        },
        "_calibration_binding": {"selected_candidate_id": 1},
    }
    original = copy.deepcopy(model)
    space = {"parameters": [{"name": "parameter", "config_path": "parameter.value"}]}
    model_path = tmp_path / "model.json"
    space_path = tmp_path / "space.json"
    output_path = tmp_path / "conditional.json"
    model_path.write_text(json.dumps(model))
    space_path.write_text(json.dumps(space))

    result = prepare(model_path, space_path, output_path)
    conditional = json.loads(output_path.read_text())
    assert result["numeric_values_changed"] == 0
    assert conditional["parameter"]["value"] == original["parameter"]["value"]
    assert conditional["parameter"]["tier"] == "fixed"
    assert "bounds" not in conditional["parameter"]
    assert "distribution" not in conditional["parameter"]
    assert validate_tier_schema(conditional) == []
