"""Unit tests for the deterministic policy tools and the decision rule.

These pin the verdict semantics: "fail" denies outright, "needs_approval"
escalates to a manager, and thresholds live in module constants the LLM cannot
override.
"""

from datetime import date, timedelta

from agents.policy.rules import decide_status, needs_manager_approval
from tools.policy import (
    MAX_TOTAL_FARE,
    MAX_TRIP_DAYS,
    MIN_ADVANCE_DAYS,
    check_advance_purchase,
    check_budget,
    check_max_trip_duration,
    check_travel_class,
)

# --- check_budget ---


def test_budget_under_cap_passes():
    res = check_budget(total_fare=1234.56)
    assert res["verdict"] == "pass"


def test_budget_exactly_at_cap_passes():
    res = check_budget(total_fare=MAX_TOTAL_FARE)
    assert res["verdict"] == "pass"


def test_budget_over_cap_fails():
    res = check_budget(total_fare=MAX_TOTAL_FARE + 0.01)
    assert res["verdict"] == "fail"
    assert "exceeds" in res["reason"]


def test_budget_without_fare_escalates():
    """No fare quote (engine down, timeout, refusal) must escalate, not pass."""
    res = check_budget()
    assert res["verdict"] == "needs_approval"
    assert "cannot be verified" in res["reason"]


# --- check_travel_class ---


def test_economy_passes():
    assert check_travel_class(requested_class="economy")["verdict"] == "pass"


def test_premium_economy_passes():
    assert check_travel_class(requested_class="premium_economy")["verdict"] == "pass"


def test_business_needs_approval():
    res = check_travel_class(requested_class="business")
    assert res["verdict"] == "needs_approval"
    assert "manager approval" in res["reason"]


def test_first_is_prohibited():
    res = check_travel_class(requested_class="first")
    assert res["verdict"] == "fail"
    assert "prohibited" in res["reason"]


def test_unknown_class_fails():
    assert check_travel_class(requested_class="suborbital")["verdict"] == "fail"


# --- check_advance_purchase ---
# Uses date.today() internally, so test dates are built relative to today.


def _days_from_today(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_advance_purchase_beyond_minimum_passes():
    res = check_advance_purchase(departure_date_str=_days_from_today(30))
    assert res["verdict"] == "pass"
    assert res["days_until_departure"] == 30


def test_advance_purchase_exactly_at_minimum_passes():
    res = check_advance_purchase(departure_date_str=_days_from_today(MIN_ADVANCE_DAYS))
    assert res["verdict"] == "pass"


def test_advance_purchase_below_minimum_fails():
    res = check_advance_purchase(
        departure_date_str=_days_from_today(MIN_ADVANCE_DAYS - 1)
    )
    assert res["verdict"] == "fail"


def test_advance_purchase_invalid_date_fails():
    res = check_advance_purchase(departure_date_str="not-a-date")
    assert res["verdict"] == "fail"
    assert "invalid" in res["reason"]


# --- check_max_trip_duration ---
# Pure date arithmetic on its arguments; fixed dates keep these deterministic.


def test_duration_within_limit_passes():
    res = check_max_trip_duration(
        departure_date_str="2026-08-01", return_date_str="2026-08-10"
    )
    assert res["verdict"] == "pass"
    assert res["trip_days"] == 9


def test_duration_exactly_at_limit_passes():
    res = check_max_trip_duration(
        departure_date_str="2026-08-01",
        return_date_str=(date(2026, 8, 1) + timedelta(days=MAX_TRIP_DAYS)).isoformat(),
    )
    assert res["verdict"] == "pass"
    assert res["trip_days"] == MAX_TRIP_DAYS


def test_duration_over_limit_fails():
    res = check_max_trip_duration(
        departure_date_str="2026-08-01",
        return_date_str=(
            date(2026, 8, 1) + timedelta(days=MAX_TRIP_DAYS + 1)
        ).isoformat(),
    )
    assert res["verdict"] == "fail"


def test_same_day_round_trip_is_a_legitimate_day_trip():
    res = check_max_trip_duration(
        departure_date_str="2026-08-01", return_date_str="2026-08-01"
    )
    assert res["verdict"] == "pass"
    assert res["trip_days"] == 0


def test_return_before_departure_fails():
    res = check_max_trip_duration(
        departure_date_str="2026-08-10", return_date_str="2026-08-01"
    )
    assert res["verdict"] == "fail"
    assert "before departure" in res["reason"]


def test_duration_invalid_date_fails():
    res = check_max_trip_duration(
        departure_date_str="2026-08-01", return_date_str="soon"
    )
    assert res["verdict"] == "fail"


# --- decide_status / needs_manager_approval ---

PASS = {"verdict": "pass", "reason": "ok"}
NEEDS = {"verdict": "needs_approval", "reason": "escalate"}
FAIL = {"verdict": "fail", "reason": "nope"}


def test_all_pass_is_approved():
    results = {"budget": PASS, "class": PASS, "advance": PASS}
    assert decide_status(results) == "approved"
    assert needs_manager_approval(results) is False


def test_any_fail_is_denied():
    results = {"budget": PASS, "class": PASS, "advance": FAIL}
    assert decide_status(results) == "denied"
    assert needs_manager_approval(results) is False


def test_needs_approval_without_fail_is_needs_review_with_escalation():
    results = {"budget": PASS, "class": NEEDS, "advance": PASS}
    assert decide_status(results) == "needs_review"
    assert needs_manager_approval(results) is True


def test_fail_beats_needs_approval():
    """An over-budget business trip is denied, not escalated."""
    results = {"budget": FAIL, "class": NEEDS}
    assert decide_status(results) == "denied"
    assert needs_manager_approval(results) is False


def test_empty_results_need_review_not_approval():
    assert decide_status({}) == "needs_review"
    assert needs_manager_approval({}) is False


def test_malformed_result_counts_as_fail():
    """A result missing its verdict must never weaken policy."""
    results = {"budget": {"reason": "no verdict key"}}
    assert decide_status(results) == "denied"


def test_engine_outage_needs_review_never_auto_approved():
    """E2E-observed failure mode: fare engine timed out, remaining checks all
    passed, and the trip was auto-approved with no budget check. The unverified
    budget must instead escalate the whole trip to a manager."""
    results = {
        "budget": check_budget(),
        "class": check_travel_class(requested_class="economy"),
    }
    assert decide_status(results) == "needs_review"
    assert needs_manager_approval(results) is True
