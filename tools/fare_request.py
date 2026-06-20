"""Deterministic translation from human-shaped intake into the fare engine's
A2A boundary contract. No LLM calls (deterministic-tool rule, see CLAUDE.md).

The traveler (and the intake agent) speak in human terms: airports, dates, cabin.
The fare engine speaks in pricing terms: distance in miles, advance-purchase days,
route type, season, booking class. The engine's own CLAUDE.md is explicit that the
*orchestrator* owns this translation ("Orchestrator derives base_distance_miles
from airports"; "the engine never knows actual airports"; "orchestrator determines
season_code from date"). This module is that translation layer.

Everything here is a static stand-in, mirroring the engine's own "static tables
stand in for real ATPCO data" stance — a real deployment would swap the airport
table for a geocoding service and the season map for a revenue-management feed.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Literal

# --- Engine boundary vocabulary (DUPLICATED ON PURPOSE) ---------------------
#
# These mirror the engine's exported vocabularies (ValidCabinClasses,
# ValidBookingClasses, ValidRouteTypes, ValidSeasonCodes, ValidPassengerTypes).
# Per the engine's CLAUDE.md, the duplication is intentional — the two repos must
# stay independently deployable — and a tripwire test on each side fails if they
# drift (here: tests/test_contract.py). If you change a value, change it in the
# engine's schema.go too.
CabinClass = Literal["economy", "premium_economy", "business", "first"]
BookingClass = Literal["Y", "B", "M", "H", "Q", "G", "K"]
RouteType = Literal["domestic", "international"]
SeasonCode = Literal["low", "shoulder", "peak"]
PassengerType = Literal["adult", "child", "infant"]

VALID_CABIN_CLASSES: list[str] = ["economy", "premium_economy", "business", "first"]
VALID_BOOKING_CLASSES: list[str] = ["Y", "B", "M", "H", "Q", "G", "K"]
VALID_ROUTE_TYPES: list[str] = ["domestic", "international"]
VALID_SEASON_CODES: list[str] = ["low", "shoulder", "peak"]
VALID_PASSENGER_TYPES: list[str] = ["adult", "child", "infant"]

# Engine numeric bounds (schema.go / DECISIONS.md §3). We clamp/validate to these
# so we never hand the engine a request it will reject.
MIN_DISTANCE_MILES = 100
MAX_DISTANCE_MILES = 10000
MAX_ADVANCE_PURCHASE_DAYS = 365
MAX_TOTAL_PASSENGERS = 9

# --- Static airport table ---------------------------------------------------
# IATA code -> (latitude, longitude, ISO country). A small but real set; unknown
# codes produce an explicit error rather than a silently-wrong fare. A production
# build would replace this with a geocoding/airport-reference service.
_AIRPORTS: dict[str, tuple[float, float, str]] = {
    "JFK": (40.6413, -73.7781, "US"),
    "LAX": (33.9416, -118.4085, "US"),
    "ORD": (41.9742, -87.9073, "US"),
    "SFO": (37.6213, -122.3790, "US"),
    "SEA": (47.4502, -122.3088, "US"),
    "DFW": (32.8998, -97.0403, "US"),
    "ATL": (33.6407, -84.4277, "US"),
    "BOS": (42.3656, -71.0096, "US"),
    "DEN": (39.8561, -104.6737, "US"),
    "MIA": (25.7959, -80.2870, "US"),
    "LHR": (51.4700, -0.4543, "GB"),
    "CDG": (49.0097, 2.5479, "FR"),
    "FRA": (50.0379, 8.5622, "DE"),
    "NRT": (35.7720, 140.3929, "JP"),
    "HND": (35.5494, 139.7798, "JP"),
    "SYD": (-33.9399, 151.1753, "AU"),
    "YYZ": (43.6777, -79.6248, "CA"),
    "YVR": (49.1967, -123.1815, "CA"),
    "MEX": (19.4361, -99.0719, "MX"),
    "GRU": (-23.4356, -46.4731, "BR"),
}


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in statute miles."""
    radius_miles = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * radius_miles * math.asin(math.sqrt(a))


def _season_for_month(month: int) -> SeasonCode:
    """Map a departure month to a seasonal pricing tier.

    Simplified Northern-Hemisphere leisure-demand calendar:
      peak     — Jun, Jul, Aug, Dec (summer + holidays)
      shoulder — Apr, May, Sep, Oct
      low      — Jan, Feb, Mar, Nov
    """
    if month in (6, 7, 8, 12):
        return "peak"
    if month in (4, 5, 9, 10):
        return "shoulder"
    return "low"


def _booking_class_for_advance(advance_days: int) -> BookingClass:
    """Assign a booking (fare) class from how far ahead the trip is booked.

    Booking earlier earns a deeper-discount class; last-minute pays full fare. The
    ladder is chosen so every selection already satisfies the engine's
    advance-purchase minimums (G≥21, Q≥14, K≥7 in the engine), so the engine never
    rejects a request this builder produced:

      < 7 days   -> Y  (full fare, refundable/changeable)
      7–13 days  -> H  (changeable, no AP minimum)
      14–20 days -> Q  (engine AP min 14 satisfied)
      >= 21 days -> G  (engine AP min 21 satisfied, deeper discount)
    """
    if advance_days >= 21:
        return "G"
    if advance_days >= 14:
        return "Q"
    if advance_days >= 7:
        return "H"
    return "Y"


def build_fare_request(
    origin: str,
    destination: str,
    departure_date: str,
    travel_class: str,
    passengers: list[dict],
    today: str | None = None,
) -> dict:
    """Derive the engine's FareQuoteRequest from human-shaped intake fields.

    Args:
        origin: Origin IATA airport code (e.g. "JFK").
        destination: Destination IATA airport code (e.g. "LHR").
        departure_date: Departure date, ISO 8601 "YYYY-MM-DD".
        travel_class: Cabin class; one of VALID_CABIN_CLASSES.
        passengers: List of {"count": int, "type": "adult"|"child"|"infant"}.
        today: Optional reference date "YYYY-MM-DD" for advance-purchase math
            (defaults to the system date; injectable for deterministic tests).

    Returns:
        On success, the FareQuoteRequest dict the engine expects, wrapped as
        ``{"ok": True, "fare_request": {...}}``. On any derivation problem,
        ``{"ok": False, "error": "<reason>"}`` — callers must surface the error
        rather than fabricate a fare.
    """
    origin = (origin or "").strip().upper()
    destination = (destination or "").strip().upper()

    if origin not in _AIRPORTS:
        return {"ok": False, "error": f"unknown origin airport {origin!r}"}
    if destination not in _AIRPORTS:
        return {"ok": False, "error": f"unknown destination airport {destination!r}"}
    if origin == destination:
        return {"ok": False, "error": "origin and destination are the same airport"}

    if travel_class not in VALID_CABIN_CLASSES:
        return {"ok": False, "error": f"unknown travel_class {travel_class!r}"}

    # Passengers: validate types/counts and the engine's total cap.
    if not passengers:
        return {"ok": False, "error": "at least one passenger group is required"}
    total = 0
    for grp in passengers:
        ptype = grp.get("type")
        count = grp.get("count")
        if ptype not in VALID_PASSENGER_TYPES:
            return {"ok": False, "error": f"unknown passenger type {ptype!r}"}
        if not isinstance(count, int) or count < 1 or count > 9:
            return {"ok": False, "error": f"passenger count {count!r} must be 1–9"}
        total += count
    if total > MAX_TOTAL_PASSENGERS:
        return {
            "ok": False,
            "error": f"total passenger count {total} exceeds engine maximum {MAX_TOTAL_PASSENGERS}",
        }

    # Dates.
    try:
        dep = datetime.strptime(departure_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {"ok": False, "error": "departure_date must be YYYY-MM-DD"}
    ref = date.today()
    if today is not None:
        try:
            ref = datetime.strptime(today, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return {"ok": False, "error": "today must be YYYY-MM-DD"}

    advance_days = (dep - ref).days
    if advance_days < 0:
        return {"ok": False, "error": "departure_date is in the past"}
    advance_days = min(advance_days, MAX_ADVANCE_PURCHASE_DAYS)

    # Distance + route type.
    olat, olon, ocountry = _AIRPORTS[origin]
    dlat, dlon, dcountry = _AIRPORTS[destination]
    raw_miles = _haversine_miles(olat, olon, dlat, dlon)
    distance = int(round(raw_miles))
    distance = max(MIN_DISTANCE_MILES, min(distance, MAX_DISTANCE_MILES))
    route_type: RouteType = "domestic" if ocountry == dcountry else "international"

    season = _season_for_month(dep.month)
    booking_class = _booking_class_for_advance(advance_days)

    fare_request = {
        "base_distance_miles": distance,
        "advance_purchase_days": advance_days,
        "passengers": [
            {"count": int(g["count"]), "type": g["type"]} for g in passengers
        ],
        "cabin_class": travel_class,
        "booking_class": booking_class,
        "route_type": route_type,
        "season_code": season,
    }
    return {"ok": True, "fare_request": fare_request}
