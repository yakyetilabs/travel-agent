"""Tests for the deterministic policy assembler (agents/policy/assembler.py).

Mirrors the finalizer assembler's harness: pure-function tests over
extract_tool_results / assemble_decision, plus agent-level runs through a
real InvocationContext. The byte-match tests pin the assembler's reasons to
the exact strings the policy evalset references bake in (under the frozen
domain clock), which is what lets the eval gate stay reference-stable with
zero evalset edits (docs/DECISIONS.md §9).
"""

import asyncio
import json

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService, Session
from google.genai import types

from agents.finalizer.assembler import parse_policy_decision
from agents.policy.assembler import (
    DECISION_STATE_KEY,
    ONE_WAY_DURATION_REASON,
    PolicyAssembler,
    assemble_decision,
    extract_tool_results,
)
from tools import clock
from tools.policy import (
    check_advance_purchase,
    check_budget,
    check_max_trip_duration,
    check_travel_class,
)

INTAKE_ROUND_TRIP = {
    "traveler": {"name": "Test Traveler"},
    "trip": {
        "origin": "JFK",
        "destination": "LAX",
        "trip_type": "round_trip",
        "departure_date": "2026-09-15",
        "return_date": "2026-09-20",
        "travel_class": "business",
    },
    "missing_fields": [],
    "ready_for_policy": True,
}

INTAKE_ONE_WAY = {
    "traveler": {"name": "Test Traveler"},
    "trip": {
        "origin": "JFK",
        "destination": "LAX",
        "trip_type": "one_way",
        "departure_date": "2026-09-10",
        "travel_class": "first",
    },
    "missing_fields": [],
    "ready_for_policy": True,
}

INTAKE_NOT_READY = {
    "traveler": {},
    "trip": {"origin": "JFK"},
    "missing_fields": ["destination", "departure_date", "travel_class"],
    "ready_for_policy": False,
}


def tool_event(
    name: str,
    response: dict,
    invocation_id: str = "e-current",
    author: str = "policy_checks",
) -> Event:
    return Event(
        invocation_id=invocation_id,
        author=author,
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name=name, response=response
                    )
                )
            ],
        ),
    )


# --- extract_tool_results ---


def test_extract_is_invocation_scoped():
    events = [
        tool_event("check_budget", {"verdict": "fail", "reason": "stale"},
                   invocation_id="e-older"),
        tool_event("check_budget", {"verdict": "pass", "reason": "current"}),
    ]
    results = extract_tool_results(events, "e-current")
    assert results == {"check_budget": {"verdict": "pass", "reason": "current"}}


def test_extract_latest_wins_per_tool():
    events = [
        tool_event("check_budget", {"verdict": "fail", "reason": "first call"}),
        tool_event("check_budget", {"verdict": "pass", "reason": "second call"}),
    ]
    results = extract_tool_results(events, "e-current")
    assert results["check_budget"]["reason"] == "second call"


def test_extract_ignores_non_policy_tools():
    events = [
        tool_event("compute_fare", {"total_fare": 288.90}),
        tool_event("check_travel_class", {"verdict": "pass", "reason": "ok"}),
    ]
    results = extract_tool_results(events, "e-current")
    assert set(results) == {"check_travel_class"}


# --- assemble_decision: statuses and flags ---


def frozen_tool_results(
    monkeypatch: pytest.MonkeyPatch,
    *,
    intake: dict,
    total_fare: float | None,
) -> dict[str, dict]:
    """Run the REAL policy tools under the frozen domain clock, exactly as the
    pipeline would for this intake, so reason strings are the tools' own."""
    monkeypatch.setenv(clock.ENV_VAR, "2026-07-07")
    trip = intake["trip"]
    results = {
        "check_budget": check_budget(total_fare=total_fare)
        if total_fare is not None
        else check_budget(),
        "check_travel_class": check_travel_class(
            requested_class=trip["travel_class"]
        ),
        "check_advance_purchase": check_advance_purchase(
            departure_date_str=trip["departure_date"]
        ),
    }
    if trip["trip_type"] == "round_trip":
        results["check_max_trip_duration"] = check_max_trip_duration(
            departure_date_str=trip["departure_date"],
            return_date_str=trip["return_date"],
        )
    return results


def test_business_cabin_matches_evalset_reference(monkeypatch):
    results = frozen_tool_results(
        monkeypatch, intake=INTAKE_ROUND_TRIP, total_fare=826.34
    )
    decision = assemble_decision(
        intake_output=INTAKE_ROUND_TRIP, tool_results=results
    )
    # Byte-for-byte the reference reasons in eval/policy.evalset.json
    # (business_cabin_needs_review), in canonical order.
    assert decision.status == "needs_review"
    assert decision.requires_manager_approval is True
    assert decision.reasons == [
        "total fare $826.34 within $2000.00 trip budget cap",
        "business requires manager approval",
        "70 days in advance, meets 7 day minimum",
        "trip duration 5 days within 14 day limit",
    ]


def test_denied_one_way_lists_passing_reasons_too(monkeypatch):
    results = frozen_tool_results(
        monkeypatch, intake=INTAKE_ONE_WAY, total_fare=711.80
    )
    decision = assemble_decision(
        intake_output=INTAKE_ONE_WAY, tool_results=results
    )
    # Reference: first_cabin_one_way_denied - a denied trip still records
    # every check that ran, plus why the duration check did not.
    assert decision.status == "denied"
    assert decision.requires_manager_approval is False
    assert decision.reasons == [
        "total fare $711.80 within $2000.00 trip budget cap",
        "first is prohibited by policy",
        "65 days in advance, meets 7 day minimum",
        ONE_WAY_DURATION_REASON,
    ]


def test_engine_outage_no_arg_budget_escalates(monkeypatch):
    intake = dict(INTAKE_ROUND_TRIP)
    intake["trip"] = dict(intake["trip"], travel_class="economy")
    results = frozen_tool_results(monkeypatch, intake=intake, total_fare=None)
    decision = assemble_decision(intake_output=intake, tool_results=results)
    assert decision.status == "needs_review"
    assert decision.requires_manager_approval is True
    assert decision.reasons[0] == (
        "no fare quote available; budget cannot be verified"
    )


def test_all_pass_approves(monkeypatch):
    intake = dict(INTAKE_ROUND_TRIP)
    intake["trip"] = dict(intake["trip"], travel_class="economy")
    results = frozen_tool_results(monkeypatch, intake=intake, total_fare=288.90)
    decision = assemble_decision(intake_output=intake, tool_results=results)
    assert decision.status == "approved"
    assert decision.requires_manager_approval is False
    assert len(decision.reasons) == 4


def test_no_tool_results_needs_review_with_missing_fields():
    decision = assemble_decision(
        intake_output=INTAKE_NOT_READY, tool_results={}
    )
    assert decision.status == "needs_review"
    assert decision.requires_manager_approval is False
    assert decision.reasons == [
        "intake incomplete; missing fields: destination, departure_date, travel_class"
    ]


def test_no_tool_results_without_missing_fields_still_never_approves():
    decision = assemble_decision(intake_output={}, tool_results={})
    assert decision.status == "needs_review"
    assert decision.reasons == ["no policy checks ran; manual review required"]


def test_malformed_tool_result_denies():
    # rules.py: a result with no verdict key counts as fail, never a pass.
    decision = assemble_decision(
        intake_output=INTAKE_ROUND_TRIP,
        tool_results={"check_budget": {"oops": True}},
    )
    assert decision.status == "denied"
    assert decision.reasons == ["check_budget: no reason recorded"]


# --- PolicyAssembler agent ---


def run_assembler(session: Session) -> list[Event]:
    agent = PolicyAssembler(name="policy_assembler")
    ctx = InvocationContext(
        session_service=InMemorySessionService(),
        invocation_id="e-current",
        agent=agent,
        session=session,
    )

    async def collect() -> list[Event]:
        return [event async for event in agent.run_async(ctx)]

    return asyncio.run(collect())


def test_agent_emits_single_event_and_string_state_delta(monkeypatch):
    results = frozen_tool_results(
        monkeypatch, intake=INTAKE_ROUND_TRIP, total_fare=826.34
    )
    session = Session(
        id="s1",
        app_name="travel-prequal-test",
        user_id="u1",
        state={"intake_output": INTAKE_ROUND_TRIP},
        events=[
            tool_event("check_budget", {"verdict": "fail", "reason": "stale"},
                       invocation_id="e-older"),
            *[tool_event(name, resp) for name, resp in results.items()],
        ],
    )
    events = run_assembler(session)

    assert len(events) == 1
    event = events[0]
    assert event.author == "policy_assembler"
    assert event.invocation_id == "e-current"

    stored = event.actions.state_delta[DECISION_STATE_KEY]
    # The downstream contract: a JSON string, not a dict, identical to the
    # emitted text (what LlmAgent output_key used to store).
    assert isinstance(stored, str)
    assert stored == event.content.parts[0].text
    decision = json.loads(stored)
    assert decision["status"] == "needs_review"
    assert decision["requires_manager_approval"] is True


def test_state_value_round_trips_through_finalizer_parser(monkeypatch):
    results = frozen_tool_results(
        monkeypatch, intake=INTAKE_ROUND_TRIP, total_fare=826.34
    )
    session = Session(
        id="s2",
        app_name="travel-prequal-test",
        user_id="u1",
        state={"intake_output": INTAKE_ROUND_TRIP},
        events=[tool_event(name, resp) for name, resp in results.items()],
    )
    stored = run_assembler(session)[0].actions.state_delta[DECISION_STATE_KEY]

    parsed = parse_policy_decision(stored)
    assert parsed is not None
    assert parsed["status"] == "needs_review"
    assert parsed["requires_manager_approval"] is True
    assert len(parsed["reasons"]) == 4
