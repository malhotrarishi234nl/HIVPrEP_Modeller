"""
test_adverse_events.py — SUPERSEDED by test_adverse_events_v2.py (V11.8 / BACKLOG Finding 3).

This file previously tested the AdverseEventModule against old V11.5.x flat-format configs.
All legacy flat-format support has been removed from AdverseEventModule. The full AE test
suite now lives in test_adverse_events_v2.py.

This file is retained only to assert that the old flat format is actively rejected.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adverse_events import AdverseEventModule


class _MockGroups:
    hiv_to_idx  = {"Susceptible": 0, "Acute": 1, "Chronic": 2, "AIDS": 3}
    prep_states = ["None", "Oral_PrEP", "Lenacapavir"]
    prep_to_idx = {"None": 0, "Oral_PrEP": 1, "Lenacapavir": 2}
    n_hiv_stages = 4


def _make_old_flat_config() -> dict:
    """V11.5.x flat-format config (no _format_version: v2)."""
    return {
        "simulation_controls": {"deterministic_mode": False},
        "adverse_events": {
            "mode": "prevalence",
            "oral_prep": {
                "ae_prevalence":        {"value": 0.15},
                "utility_decrement":    {"value": -0.02},
                "annual_cost_addition": {"value": 200},
            },
        },
    }


class TestLegacyFormatRejected:
    """
    V11.8 / BACKLOG Finding 3: old flat-format hta_config must raise ValueError at
    AdverseEventModule construction time. The legacy normalization shim has been removed.
    """

    def test_old_flat_format_raises_value_error(self):
        """Passing old flat-format config (no _format_version: v2) raises ValueError."""
        with pytest.raises(ValueError, match="v2"):
            AdverseEventModule(
                hta_config=_make_old_flat_config(),
                groups=_MockGroups(),
                step_fraction=0.25,
            )
