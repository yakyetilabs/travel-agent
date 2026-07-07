"""Unit tests for the intake schema passenger rules.

Intake mirrors the engine's passenger rules (seated cap, one lap infant per
adult) so an impossible trip fails fast at intake instead of surviving until
the engine rejects it.
"""

import pytest
from pydantic import ValidationError

from agents.intake.schemas import TripRequest


def _trip(passengers: list[dict]) -> TripRequest:
    return TripRequest(passengers=passengers)


def test_nine_seated_passengers_accepted():
    trip = _trip([{"count": 5, "type": "adult"}, {"count": 4, "type": "child"}])
    assert sum(g.count for g in trip.passengers) == 9


def test_ten_seated_passengers_rejected():
    with pytest.raises(ValidationError, match="seated passenger count"):
        _trip([{"count": 6, "type": "adult"}, {"count": 4, "type": "child"}])


def test_lap_infants_do_not_count_toward_seat_cap():
    trip = _trip(
        [
            {"count": 5, "type": "adult"},
            {"count": 4, "type": "child"},
            {"count": 5, "type": "infant"},
        ]
    )
    assert sum(g.count for g in trip.passengers) == 14


def test_more_infants_than_adults_rejected():
    with pytest.raises(ValidationError, match="one lap infant per adult"):
        _trip([{"count": 1, "type": "adult"}, {"count": 2, "type": "infant"}])


def test_infant_only_rejected():
    with pytest.raises(ValidationError, match="one lap infant per adult"):
        _trip([{"count": 1, "type": "infant"}])
