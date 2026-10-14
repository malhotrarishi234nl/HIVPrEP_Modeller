from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stochastic_allocation import deterministic_allocate_events  # noqa: E402
from tools.run_a12_deterministic_allocation_audit import (  # noqa: E402
    INVENTORY,
    VALID_CLASSIFICATIONS,
    locate_inventory,
    syntax_candidates,
)


def test_inventory_covers_every_required_a12_process():
    observed = {row["process"] for row in INVENTORY}
    required_terms = {
        "transmission",
        "progression",
        "mortality",
        "ART",
        "diagnosis",
        "PrEP",
        "migration",
        "aging",
        "birth",
        "debut",
    }
    for term in required_terms:
        assert any(term.lower() in process.lower() for process in observed), term


def test_inventory_anchors_resolve_and_classifications_are_complete():
    located = locate_inventory(INVENTORY)
    assert len(located) == len(INVENTORY)
    assert len({row["site_id"] for row in located}) == len(located)
    assert all(row["classification"] in VALID_CLASSIFICATIONS for row in located)
    assert all(row["source_line"] > 0 for row in located)


def test_every_cellwise_site_has_quantified_base_case_differential_and_action():
    cellwise = [row for row in INVENTORY if row["classification"] == "cellwise"]
    assert cellwise
    assert all(row["fragmentation_sensitive"] for row in cellwise)
    assert all(row["between_arm_event_differential_people"] is not None for row in cellwise)
    assert all(row["recommendation"] for row in cellwise)


def test_cellwise_rounding_is_fragmentation_sensitive_but_shared_allocator_is_not():
    compact = np.array([2], dtype=np.int64)
    fragmented = np.array([1, 1], dtype=np.int64)
    probability = 0.4
    naive_compact = int(np.round(compact * probability).sum())
    naive_fragmented = int(np.round(fragmented * probability).sum())
    assert naive_compact == 1
    assert naive_fragmented == 0
    assert int(deterministic_allocate_events(compact, probability).sum()) == 1
    assert int(deterministic_allocate_events(fragmented, probability).sum()) == 1


def test_syntax_candidate_scan_is_nonempty_and_scoped_to_reviewed_files():
    candidates = syntax_candidates()
    assert candidates
    assert all((ROOT / row["module"]).is_file() for row in candidates)
    assert {"round", "int", "floor", "astype"}.issubset(
        {row["syntax"] for row in candidates}
    )
    assert all(row["disposition"] for row in candidates)
