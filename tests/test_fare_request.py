"""Unit tests for the deterministic intake -> FareQuoteRequest builder.

These pin the derivation logic and, crucially, assert that every request the
builder emits already satisfies the fare engine's validation bounds — so the two
services connect without the engine ever rejecting our input.
"""

import pytest

from tools.fare_request import (
    MAX_ADVANCE_PURCHASE_DAYS,
    MAX_DISTANCE_MILES,
    MIN_DISTANCE_MILES,
    VALID_BOOKING_CLASSES,
    build_fare_request,
)

# Engine booking-class advance-purchase minimums (engine: BookingAdvancePurchaseMin).
ENGINE_AP_MIN = {"G": 21, "Q": 14, "K": 7}


def _ok(**kwargs):
    kwargs.setdefault("origin", "JFK")
    kwargs.setdefault("destination", "LAX")
    kwargs.setdefault("departure_date", "2026-07-15")
    kwargs.setdefault("travel_class", "economy")
    kwargs.setdefault("passengers", [{"count": 1, "type": "adult"}])
    kwargs.setdefault("today", "2026-01-01")
    res = build_fare_request(**kwargs)
    assert res["ok"], res
    return res["fare_request"]


def test_domestic_vs_international_route_type():
    assert _ok(origin="JFK", destination="LAX")["route_type"] == "domestic"
    assert _ok(origin="JFK", destination="LHR")["route_type"] == "international"


def test_season_from_departure_month():
    assert _ok(departure_date="2026-07-10")["season_code"] == "peak"  # summer
    assert _ok(departure_date="2026-12-24")["season_code"] == "peak"  # holidays
    assert _ok(departure_date="2026-05-10")["season_code"] == "shoulder"
    assert _ok(departure_date="2026-02-10")["season_code"] == "low"


@pytest.mark.parametrize(
    "advance_days,expected_class",
    [(3, "Y"), (10, "H"), (17, "Q"), (40, "G")],
)
def test_booking_class_ladder(advance_days, expected_class):
    # today fixed; departure = today + advance_days
    from datetime import date, timedelta

    dep = (date(2026, 1, 1) + timedelta(days=advance_days)).isoformat()
    fr = _ok(departure_date=dep, today="2026-01-01")
    assert fr["booking_class"] == expected_class


def test_every_booking_class_choice_satisfies_engine_ap_minimum():
    """The builder must never emit a class whose AP minimum it violates."""
    from datetime import date, timedelta

    for advance_days in range(0, 366, 1):
        dep = (date(2026, 1, 1) + timedelta(days=advance_days)).isoformat()
        fr = _ok(departure_date=dep, today="2026-01-01")
        cls = fr["booking_class"]
        assert cls in VALID_BOOKING_CLASSES
        if cls in ENGINE_AP_MIN:
            assert fr["advance_purchase_days"] >= ENGINE_AP_MIN[cls], (
                f"{advance_days}d -> class {cls} but AP {fr['advance_purchase_days']} "
                f"< engine min {ENGINE_AP_MIN[cls]}"
            )


def test_distance_within_engine_bounds():
    # Very close pair clamps up to the floor; far pair stays under the ceiling.
    near = _ok(origin="JFK", destination="BOS")  # ~185mi
    assert MIN_DISTANCE_MILES <= near["base_distance_miles"] <= MAX_DISTANCE_MILES
    far = _ok(origin="JFK", destination="SYD")  # ~9950mi
    assert MIN_DISTANCE_MILES <= far["base_distance_miles"] <= MAX_DISTANCE_MILES


def test_advance_days_clamped_to_max():
    fr = _ok(departure_date="2030-01-01", today="2026-01-01")  # >365 days out
    assert fr["advance_purchase_days"] == MAX_ADVANCE_PURCHASE_DAYS


@pytest.mark.parametrize(
    "kwargs,err_substr",
    [
        ({"destination": "XXX"}, "unknown destination"),
        ({"origin": "XXX"}, "unknown origin"),
        ({"origin": "JFK", "destination": "JFK"}, "same airport"),
        ({"departure_date": "nope"}, "YYYY-MM-DD"),
        ({"departure_date": "2025-01-01"}, "in the past"),
        ({"travel_class": "luxury"}, "unknown travel_class"),
        ({"passengers": []}, "at least one passenger"),
        ({"passengers": [{"count": 0, "type": "adult"}]}, "must be 1"),
        (
            {"passengers": [{"count": 9, "type": "adult"}, {"count": 2, "type": "child"}]},
            "exceeds engine maximum",
        ),
    ],
)
def test_error_paths(kwargs, err_substr):
    res = build_fare_request(
        **{
            "origin": "JFK",
            "destination": "LAX",
            "departure_date": "2026-07-15",
            "travel_class": "economy",
            "passengers": [{"count": 1, "type": "adult"}],
            "today": "2026-01-01",
            **kwargs,
        }
    )
    assert not res["ok"]
    assert err_substr in res["error"], res
