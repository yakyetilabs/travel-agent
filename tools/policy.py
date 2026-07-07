"""Deterministic corporate travel policy tools. No LLM calls.

Every check returns {"verdict": "pass" | "needs_approval" | "fail", "reason": ...}.
"fail" means the trip is out of policy outright; "needs_approval" means the trip
is allowed only with manager sign-off (pre-trip approval escalates, it does not
flat-deny everything unusual).

Policy thresholds are module constants, deliberately NOT tool parameters: the
LLM decides when to call these tools but cannot pass — and therefore cannot
weaken — any threshold.
"""

from datetime import date, datetime

# Trip budget cap: the quoted journey total (all legs, all passengers on the
# booking, taxes included). Guest travelers count against the same cap.
MAX_TOTAL_FARE = 2000.0
MIN_ADVANCE_DAYS = 7
MAX_TRIP_DAYS = 14

# Cabin policy: business escalates to a manager; first is prohibited outright.
CABIN_VERDICTS = {
    "economy": "pass",
    "premium_economy": "pass",
    "business": "needs_approval",
    "first": "fail",
}


def check_budget(total_fare: float | None = None) -> dict:
    """Check the engine-computed total fare against the trip budget cap.

    This runs AFTER the fare engine in the pipeline, so `total_fare` is the real
    quoted journey total (all passengers, taxes included) — not an estimate.
    Calling it before the fare exists is meaningless, which is why policy is
    sequenced after the fare engine.

    Call it with NO argument when the engine produced no quote (unreachable,
    timed out, or refused to price). An unverifiable budget escalates to a
    manager instead of silently passing: a mandatory check that cannot run
    must never count as a pass.
    """
    if total_fare is None:
        return {
            "verdict": "needs_approval",
            "reason": "no fare quote available; budget cannot be verified",
        }
    if total_fare <= MAX_TOTAL_FARE:
        return {
            "verdict": "pass",
            "reason": f"total fare ${total_fare:.2f} within ${MAX_TOTAL_FARE:.2f} trip budget cap",
        }
    return {
        "verdict": "fail",
        "reason": f"total fare ${total_fare:.2f} exceeds ${MAX_TOTAL_FARE:.2f} trip budget cap",
    }


def check_travel_class(requested_class: str) -> dict:
    verdict = CABIN_VERDICTS.get(requested_class)
    if verdict == "pass":
        return {"verdict": "pass", "reason": f"{requested_class} is within policy"}
    if verdict == "needs_approval":
        return {
            "verdict": "needs_approval",
            "reason": f"{requested_class} requires manager approval",
        }
    if verdict == "fail":
        return {"verdict": "fail", "reason": f"{requested_class} is prohibited by policy"}
    return {
        "verdict": "fail",
        "reason": f"unknown travel class {requested_class!r}",
    }


def check_advance_purchase(departure_date_str: str) -> dict:
    try:
        departure = datetime.strptime(departure_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {
            "verdict": "fail",
            "reason": "invalid departure date format, use YYYY-MM-DD",
        }
    days_until = (departure - date.today()).days
    if days_until >= MIN_ADVANCE_DAYS:
        return {
            "verdict": "pass",
            "days_until_departure": days_until,
            "reason": f"{days_until} days in advance, meets {MIN_ADVANCE_DAYS} day minimum",
        }
    return {
        "verdict": "fail",
        "days_until_departure": days_until,
        "reason": f"only {days_until} days in advance, minimum {MIN_ADVANCE_DAYS} required",
    }


def check_max_trip_duration(departure_date_str: str, return_date_str: str) -> dict:
    try:
        dep = datetime.strptime(departure_date_str, "%Y-%m-%d").date()
        ret = datetime.strptime(return_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {"verdict": "fail", "reason": "invalid date format, use YYYY-MM-DD"}
    if ret < dep:
        return {"verdict": "fail", "reason": "return date is before departure"}
    # A same-day round trip (0 days) is a legitimate day trip.
    trip_days = (ret - dep).days
    if trip_days <= MAX_TRIP_DAYS:
        return {
            "verdict": "pass",
            "trip_days": trip_days,
            "reason": f"trip duration {trip_days} days within {MAX_TRIP_DAYS} day limit",
        }
    return {
        "verdict": "fail",
        "trip_days": trip_days,
        "reason": f"trip duration {trip_days} exceeds {MAX_TRIP_DAYS} day limit",
    }
