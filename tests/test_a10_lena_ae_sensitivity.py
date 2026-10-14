from pathlib import Path
import copy
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from hta_config_loader import load_hta_config  # noqa: E402
from run_a10_lena_ae_sensitivity import (  # noqa: E402
    PERSISTENT_ID,
    TRANSIENT_ID,
    audit_oral_prep_ae,
    delta_qaly_from_weights,
    extract_lena_ae_inputs,
    parameter_flip_weight,
    set_zero_lena_ae_weights,
)


@pytest.fixture()
def live_hta_config():
    return load_hta_config(ROOT / "configs" / "hta_runtime_config.json")


def test_live_base_case_has_no_active_oral_prep_ae_and_complete_citations(
    live_hta_config,
):
    audit = audit_oral_prep_ae(live_hta_config)
    assert audit["active_oral_ae_item_count"] == 0
    assert audit["susceptible_oral_prep_utility"] == 1.0
    assert audit["missing_citation_tokens"] == []
    assert audit["evidence_status"] == "weakly_supported"


def test_oral_prep_ae_audit_rejects_active_item(live_hta_config):
    cfg = copy.deepcopy(live_hta_config)
    cfg["adverse_events"]["items"].append(
        {
            "id": "unsupported_oral_ae",
            "arm": "oral_prep",
            "billing_moment": "ongoing",
        }
    )
    with pytest.raises(AssertionError, match="active oral-PrEP AE items"):
        audit_oral_prep_ae(cfg)


def test_configured_lena_inputs_define_beta_quantile_sweep(live_hta_config):
    inputs = extract_lena_ae_inputs(live_hta_config)
    assert set(inputs) == {TRANSIENT_ID, PERSISTENT_ID}
    assert inputs[TRANSIENT_ID]["configured_weight_magnitude"] == 0.01
    assert inputs[PERSISTENT_ID]["configured_weight_magnitude"] == 0.02
    for item in inputs.values():
        assert item["distribution_support_lower"] == 0.0
        assert item["distribution_support_upper"] == 1.0
        assert 0.0 < item["q025"] < item["q50"] < item["q975"] < 1.0
        assert item["sweep_lower"] == 0.0
        assert item["sweep_upper"] == 1.0
        assert item["decision_focused_upper"] == item["q975"]
        assert "not_empirical_plausible_bounds" in item["range_status"]


def test_zero_lena_ae_mutation_changes_only_two_weight_values(live_hta_config):
    before = copy.deepcopy(live_hta_config)
    after = copy.deepcopy(live_hta_config)
    set_zero_lena_ae_weights(after)
    before_items = {item["id"]: item for item in before["adverse_events"]["items"]}
    after_items = {item["id"]: item for item in after["adverse_events"]["items"]}
    for item_id in (TRANSIENT_ID, PERSISTENT_ID):
        assert after_items[item_id]["utility_decrement"]["value"] == 0.0
        after_items[item_id]["utility_decrement"]["value"] = before_items[item_id][
            "utility_decrement"
        ]["value"]
    assert after == before


def test_qaly_formula_and_flip_are_exact():
    no_ae_delta = 10.0
    exposure = 1000.0
    transient_prevalence = 0.4
    persistent_prevalence = 0.3
    persistent_weight = 0.02
    transient_flip = parameter_flip_weight(
        no_ae_delta_qaly=no_ae_delta,
        discounted_active_lena_py=exposure,
        varied_prevalence=transient_prevalence,
        fixed_prevalence=persistent_prevalence,
        fixed_weight=persistent_weight,
    )
    assert transient_flip == pytest.approx(0.01)
    at_flip = delta_qaly_from_weights(
        no_ae_delta,
        exposure,
        transient_prevalence,
        transient_flip,
        persistent_prevalence,
        persistent_weight,
    )
    assert at_flip == pytest.approx(0.0)
    assert delta_qaly_from_weights(
        no_ae_delta,
        exposure,
        transient_prevalence,
        transient_flip / 2,
        persistent_prevalence,
        persistent_weight,
    ) > 0
    assert delta_qaly_from_weights(
        no_ae_delta,
        exposure,
        transient_prevalence,
        transient_flip * 2,
        persistent_prevalence,
        persistent_weight,
    ) < 0
