from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "documentation/reports/calibration/robust_nroy_readiness_2026-10-15"
PACKET = PACKAGE / "OWNER_DECISION_PACKET.md"
REGISTER = PACKAGE / "DECISION_REGISTER.csv"
PLAN = ROOT / "configs/robust_nroy_freeze_production.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_owner_packet_covers_bound_register_exactly_once():
    text = PACKET.read_text()
    with REGISTER.open(newline="") as handle:
        expected = {
            index: row["dotted_plan_path"]
            for index, row in enumerate(csv.DictReader(handle), 1)
        }
    found = {
        int(index): path
        for index, path in re.findall(r"^\| (\d+) \| `([^`]+)` \|", text, re.MULTILINE)
    }
    assert len(found) == 43
    assert found == expected
    assert _sha256(PLAN) in text
    assert _sha256(REGISTER) in text


def test_owner_packet_is_linked_from_current_entry_points():
    for path in (ROOT / "README.md", ROOT / "documentation/INDEX.md", PACKAGE / "README.md"):
        assert "OWNER_DECISION_PACKET.md" in path.read_text()
