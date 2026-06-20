"""Deterministic corporate travel policy tools. No LLM calls."""

from datetime import date, datetime

ALLOWED_CLASSES = ["economy", "premium_economy"]
DEFAULT_MAX_FARE = 2000.0
DEFAULT_MIN_ADVANCE_DAYS = 7
DEFAULT_MAX_TRIP_DAYS = 14


def check_budget(
    total_fare: float, max_total_fare: float = DEFAULT_MAX_FARE
) -> dict:
    """Check the engine-computed total fare against the corporate cap.

    This runs AFTER the fare engine in the pipeline, so `total_fare` is the real
    quoted total (all passengers, taxes included) — not an estimate. Calling it
    before the fare exists is meaningless, which is why policy is sequenced after
    the fare engine.
    """
    if total_fare <= max_total_fare:
        return {
            "allowed": True,
            "reason": f"total fare ${total_fare:.2f} within ${max_total_fare:.2f} cap",
        }
    return {
        "allowed": False,
        "reason": f"total fare ${total_fare:.2f} exceeds ${max_total_fare:.2f} cap",
    }


def check_travel_class(
    requested_class: str, allowed_classes: list[str] = ALLOWED_CLASSES
) -> dict:
    if requested_class in allowed_classes:
        return {"allowed": True, "reason": f"{requested_class} is allowed"}
    else:
        return {
            "allowed": False,
            "reason": f"{requested_class} not in allowed classes {allowed_classes}",
        }


def check_advance_purchase(
    departure_date_str: str, min_days: int = DEFAULT_MIN_ADVANCE_DAYS
) -> dict:
    try:
        departure = datetime.strptime(departure_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {
            "allowed": False,
            "reason": "invalid departure date format, use YYYY-MM-DD",
        }
    days_until = (departure - date.today()).days
    if days_until >= min_days:
        return {
            "allowed": True,
            "days_until_departure": days_until,
            "reason": f"{days_until} days in advance, meets {min_days} day minimum",
        }
    else:
        return {
            "allowed": False,
            "days_until_departure": days_until,
            "reason": f"only {days_until} days in advance, minimum {min_days} required",
        }


def check_max_trip_duration(
    departure_date_str: str, return_date_str: str, max_days: int = DEFAULT_MAX_TRIP_DAYS
) -> dict:
    try:
        dep = datetime.strptime(departure_date_str, "%Y-%m-%d").date()
        ret = datetime.strptime(return_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {"allowed": False, "reason": "invalid date format, use YYYY-MM-DD"}
    if dep >= ret:
        return {"allowed": False, "reason": "departure must be before return"}
    trip_days = (ret - dep).days
    if trip_days <= max_days:
        return {
            "allowed": True,
            "trip_days": trip_days,
            "reason": f"trip duration {trip_days} days within {max_days} day limit",
        }
    else:
        return {
            "allowed": False,
            "trip_days": trip_days,
            "reason": f"trip duration {trip_days} exceeds {max_days} day limit",
        }
