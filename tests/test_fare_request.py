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
    kwargs.setdefault("trip_type", "one_way")
    kwargs.setdefault("departure_date", "2026-07-15")
    kwargs.setdefault("travel_class", "economy")
    kwargs.setdefault("passengers", [{"count": 1, "type": "adult"}])
    kwargs.setdefault("today", "2026-01-01")
    res = build_fare_request(**kwargs)
    assert res["ok"], res
    return res["fare_request"]


def test_one_way_has_single_outbound_component():
    fr = _ok()
    assert fr["journey_type"] == "one_way"
    assert len(fr["fare_components"]) == 1
    assert fr["fare_components"][0]["direction"] == "outbound"


def test_round_trip_has_outbound_and_return_components():
    fr = _ok(trip_type="round_trip", return_date="2026-07-25")
    assert fr["journey_type"] == "round_trip"
    assert [c["direction"] for c in fr["fare_components"]] == ["outbound", "return"]
    # Symmetric route: both legs share distance; each leg has its own dates.
    out, ret = fr["fare_components"]
    assert out["base_distance_miles"] == ret["base_distance_miles"]
    assert out["advance_purchase_days"] == 195  # 2026-01-01 -> 2026-07-15
    assert ret["advance_purchase_days"] == 205  # 2026-01-01 -> 2026-07-25


def test_round_trip_legs_derive_independently():
    """Each leg gets its own season, AP tier, and booking class from its own date."""
    fr = _ok(
        trip_type="round_trip",
        departure_date="2026-01-11",  # low season, 10 AP days -> H
        return_date="2026-04-05",  # shoulder season, 94 AP days -> G
        today="2026-01-01",
    )
    out, ret = fr["fare_components"]
    assert out["season_code"] == "low"
    assert out["booking_class"] == "H"
    assert ret["season_code"] == "shoulder"
    assert ret["booking_class"] == "G"


def test_same_day_return_is_buildable():
    # A same-day round trip is a valid fare construction; policy may still
    # reject it, but the builder must not.
    fr = _ok(trip_type="round_trip", departure_date="2026-07-15", return_date="2026-07-15")
    assert [c["direction"] for c in fr["fare_components"]] == ["outbound", "return"]


def test_domestic_vs_international_route_type():
    assert _ok(origin="JFK", destination="LAX")["route_type"] == "domestic"
    assert _ok(origin="JFK", destination="LHR")["route_type"] == "international"


def test_season_from_departure_month():
    def season(**kw):
        return _ok(**kw)["fare_components"][0]["season_code"]

    assert season(departure_date="2026-07-10") == "peak"  # summer
    assert season(departure_date="2026-12-24") == "peak"  # holidays
    assert season(departure_date="2026-05-10") == "shoulder"
    assert season(departure_date="2026-02-10") == "low"


@pytest.mark.parametrize(
    "advance_days,expected_class",
    [(3, "Y"), (10, "H"), (17, "Q"), (40, "G")],
)
def test_booking_class_ladder(advance_days, expected_class):
    # today fixed; departure = today + advance_days
    from datetime import date, timedelta

    dep = (date(2026, 1, 1) + timedelta(days=advance_days)).isoformat()
    fr = _ok(departure_date=dep, today="2026-01-01")
    assert fr["fare_components"][0]["booking_class"] == expected_class


def test_every_booking_class_choice_satisfies_engine_ap_minimum():
    """The builder must never emit a component whose booking class violates the
    engine's AP minimum — checked per leg, across the full advance range."""
    from datetime import date, timedelta

    for advance_days in range(0, 366, 1):
        dep = (date(2026, 1, 1) + timedelta(days=advance_days)).isoformat()
        ret = (date(2026, 1, 1) + timedelta(days=advance_days + 5)).isoformat()
        fr = _ok(
            trip_type="round_trip",
            departure_date=dep,
            return_date=ret,
            today="2026-01-01",
        )
        for comp in fr["fare_components"]:
            cls = comp["booking_class"]
            assert cls in VALID_BOOKING_CLASSES
            if cls in ENGINE_AP_MIN:
                assert comp["advance_purchase_days"] >= ENGINE_AP_MIN[cls], (
                    f"{advance_days}d -> {comp['direction']} class {cls} but AP "
                    f"{comp['advance_purchase_days']} < engine min {ENGINE_AP_MIN[cls]}"
                )


def test_distance_within_engine_bounds():
    # Very close pair clamps up to the floor; far pair stays under the ceiling.
    near = _ok(origin="JFK", destination="BOS")  # ~185mi
    far = _ok(origin="JFK", destination="SYD")  # ~9950mi
    for fr in (near, far):
        d = fr["fare_components"][0]["base_distance_miles"]
        assert MIN_DISTANCE_MILES <= d <= MAX_DISTANCE_MILES


def test_advance_days_clamped_to_max():
    fr = _ok(departure_date="2030-01-01", today="2026-01-01")  # >365 days out
    assert fr["fare_components"][0]["advance_purchase_days"] == MAX_ADVANCE_PURCHASE_DAYS


def test_lap_infants_do_not_count_toward_seat_cap():
    # 5 adults + 4 children fill all nine seats; 5 lap infants are still valid.
    fr = _ok(
        passengers=[
            {"count": 5, "type": "adult"},
            {"count": 4, "type": "child"},
            {"count": 5, "type": "infant"},
        ]
    )
    assert sum(g["count"] for g in fr["passengers"]) == 14


@pytest.mark.parametrize(
    "kwargs,err_substr",
    [
        ({"destination": "XXX"}, "unknown destination"),
        ({"origin": "XXX"}, "unknown origin"),
        ({"origin": "JFK", "destination": "JFK"}, "same airport"),
        ({"departure_date": "nope"}, "YYYY-MM-DD"),
        ({"departure_date": "2025-01-01"}, "in the past"),
        ({"travel_class": "luxury"}, "unknown travel_class"),
        ({"trip_type": "multi_city"}, "unknown trip_type"),
        ({"trip_type": "one_way", "return_date": "2026-07-20"}, "one_way"),
        ({"trip_type": "round_trip"}, "return_date is required"),
        ({"trip_type": "round_trip", "return_date": "nope"}, "YYYY-MM-DD"),
        (
            {"trip_type": "round_trip", "return_date": "2026-07-10"},
            "before departure_date",
        ),
        ({"passengers": []}, "at least one passenger"),
        ({"passengers": [{"count": 0, "type": "adult"}]}, "must be 1"),
        (
            {"passengers": [{"count": 9, "type": "adult"}, {"count": 2, "type": "child"}]},
            "exceeds engine maximum",
        ),
        (
            {"passengers": [{"count": 1, "type": "adult"}, {"count": 2, "type": "infant"}]},
            "one lap infant per adult",
        ),
        (
            {"passengers": [{"count": 1, "type": "infant"}]},
            "one lap infant per adult",
        ),
    ],
)
def test_error_paths(kwargs, err_substr):
    res = build_fare_request(
        **{
            "origin": "JFK",
            "destination": "LAX",
            "trip_type": "one_way",
            "departure_date": "2026-07-15",
            "travel_class": "economy",
            "passengers": [{"count": 1, "type": "adult"}],
            "today": "2026-01-01",
            **kwargs,
        }
    )
    assert not res["ok"]
    assert err_substr in res["error"], res
