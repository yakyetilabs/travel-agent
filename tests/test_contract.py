"""Cross-repo boundary-contract tripwire (orchestrator side).

The fare engine and this orchestrator each hardcode the same enum vocabularies on
purpose (the two repos must stay independently deployable — no shared import). The
engine's CLAUDE.md says: "If you add a new value, you must also update the
orchestrator's duplicate Literal in the other repo." This test is that enforcement
on the orchestrator side:

1. It pins this repo's duplicated vocabulary to the agreed contract, so changing a
   value here is a conscious, reviewed edit (mirrors the engine's TestTripwire).
2. If the engine's agent-card.json is reachable on disk (sibling checkout, or via
   the FARE_ENGINE_CARD env var), it also asserts the two sides actually match —
   catching real drift between the repos.
"""

import json
import os
from pathlib import Path

import pytest

from tools.fare_request import (
    VALID_BOOKING_CLASSES,
    VALID_CABIN_CLASSES,
    VALID_PASSENGER_TYPES,
    VALID_ROUTE_TYPES,
    VALID_SEASON_CODES,
)

# The agreed contract. Must equal the engine's exported slices in schema.go.
EXPECTED = {
    "cabin_class": ["economy", "premium_economy", "business", "first"],
    "booking_class": ["Y", "B", "M", "H", "Q", "G", "K"],
    "route_type": ["domestic", "international"],
    "season_code": ["low", "shoulder", "peak"],
    "passenger_type": ["adult", "child", "infant"],
}

ACTUAL = {
    "cabin_class": VALID_CABIN_CLASSES,
    "booking_class": VALID_BOOKING_CLASSES,
    "route_type": VALID_ROUTE_TYPES,
    "season_code": VALID_SEASON_CODES,
    "passenger_type": VALID_PASSENGER_TYPES,
}


@pytest.mark.parametrize("field", sorted(EXPECTED))
def test_local_vocabulary_pinned(field: str) -> None:
    assert ACTUAL[field] == EXPECTED[field], (
        f"{field}: this repo has {ACTUAL[field]} but the contract is {EXPECTED[field]}. "
        "If this is intentional, update the engine's schema.go too."
    )


def _find_engine_card() -> Path | None:
    env = os.environ.get("FARE_ENGINE_CARD")
    if env and Path(env).is_file():
        return Path(env)
    sibling = Path(__file__).resolve().parents[2] / "travel-fare-engine" / "agent-card.json"
    return sibling if sibling.is_file() else None


def test_matches_engine_agent_card() -> None:
    card_path = _find_engine_card()
    if card_path is None:
        pytest.skip(
            "engine agent-card.json not found; set FARE_ENGINE_CARD to enable the "
            "live cross-repo drift check"
        )
    card = json.loads(card_path.read_text())
    props = card["skills"][0]["inputSchema"]["properties"]

    assert props["cabin_class"]["enum"] == VALID_CABIN_CLASSES
    assert props["booking_class"]["enum"] == VALID_BOOKING_CLASSES
    assert props["route_type"]["enum"] == VALID_ROUTE_TYPES
    assert props["season_code"]["enum"] == VALID_SEASON_CODES
    assert props["passengers"]["items"]["properties"]["type"]["enum"] == VALID_PASSENGER_TYPES
