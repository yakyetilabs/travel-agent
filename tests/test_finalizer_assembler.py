"""Unit tests for the finalizer's deterministic assembly.

Same spirit as the engine's passthrough tests: the structured output must be
a byte-faithful function of upstream data. The fixture quote is a REAL
engine response (JFK-LAX round trip, $288.90 - the production-verified
fare), so the fare_rules regression (populated from the engine, nulled by
the old LLM transcription) is pinned against authentic data.
"""

import asyncio
import json

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService, Session
from google.genai import types

from agents.finalizer.assembler import (
    FARE_ENGINE_AUTHOR,
    OUTPUT_STATE_KEY,
    SUMMARY_STATE_KEY,
    FinalizerAssembler,
    assemble_output,
    extract_fare_quote,
    parse_policy_decision,
)

# A complete, unedited engine response captured from the deployed engine
# (invocation e-1ba7477d, 2026-07-08). Full-dict equality below is the
# regression test: nothing may be lost or retyped in assembly.
ENGINE_QUOTE = {
    "base_fare": 249.96,
    "currency": "USD",
    "expires_at": "2026-07-09T03:00:13Z",
    "fare_components": [
        {
            "base_fare": 124.98,
            "booking_class": "G",
            "direction": "outbound",
            "fare_basis_code": "EGSD05",
            "fare_rules": {
                "advance_purchase_min": 21,
                "changeable": False,
                "refundable": False,
            },
        },
        {
            "base_fare": 124.98,
            "booking_class": "G",
            "direction": "return",
            "fare_basis_code": "EGSD05",
            "fare_rules": {
                "advance_purchase_min": 21,
                "changeable": False,
                "refundable": False,
            },
        },
    ],
    "journey_type": "round_trip",
    "pricing_breakdown": [
        "Outbound base fare: 2470 miles * $0.0575/mile = $142.03",
        "Outbound advance purchase discount (70 days): -$17.04 (12%)",
        "Outbound 1 adult(s): $124.98",
        "Outbound U.S. Transportation Tax (adult): $9.37",
        "Outbound Passenger Facility Charge (adult): $4.50",
        "Outbound September 11th Security Fee (adult): $5.60",
        "Return base fare: 2470 miles * $0.0575/mile = $142.03",
        "Return advance purchase discount (75 days): -$17.04 (12%)",
        "Return 1 adult(s): $124.98",
        "Return U.S. Transportation Tax (adult): $9.37",
        "Return Passenger Facility Charge (adult): $4.50",
        "Return September 11th Security Fee (adult): $5.60",
        "Journey total: $288.90",
    ],
    "quote_id": "10547c7864846876f2f60e2be013d430",
    "taxes": [
        {"amount": 9.37, "code": "US", "name": "U.S. Transportation Tax"},
        {"amount": 4.5, "code": "XF", "name": "Passenger Facility Charge"},
        {"amount": 5.6, "code": "AY", "name": "September 11th Security Fee"},
        {"amount": 9.37, "code": "US", "name": "U.S. Transportation Tax"},
        {"amount": 4.5, "code": "XF", "name": "Passenger Facility Charge"},
        {"amount": 5.6, "code": "AY", "name": "September 11th Security Fee"},
    ],
    "total_fare": 288.9,
}

INTAKE_READY = {
    "traveler": {
        "name": "Test Traveler",
        "email": "test@example.com",
        "employee_id": "00001",
        "department": "Engineering",
    },
    "trip": {
        "origin": "JFK",
        "destination": "LAX",
        "trip_type": "round_trip",
        "departure_date": "2026-09-15",
        "return_date": "2026-09-20",
        "passengers": [{"count": 1, "type": "adult"}],
        "travel_class": "economy",
        "trip_purpose": "client_meeting",
    },
    "missing_fields": [],
    "ready_for_policy": True,
}

POLICY_APPROVED_TEXT = json.dumps(
    {
        "status": "approved",
        "reasons": ["total fare $288.90 within $2500.00 trip budget cap"],
        "requires_manager_approval": False,
    }
)


def engine_event(
    text: str, invocation_id: str = "e-current", author: str = FARE_ENGINE_AUTHOR
) -> Event:
    return Event(
        invocation_id=invocation_id,
        author=author,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


# --- extract_fare_quote ---


def test_extract_returns_quote_verbatim_including_fare_rules():
    events = [engine_event(json.dumps(ENGINE_QUOTE))]
    quote = extract_fare_quote(events, "e-current")
    assert quote == ENGINE_QUOTE
    for component in quote["fare_components"]:
        assert component["fare_rules"] == {
            "advance_purchase_min": 21,
            "changeable": False,
            "refundable": False,
        }


def test_extract_ignores_stale_quote_from_older_invocation():
    stale = dict(ENGINE_QUOTE, quote_id="stale-quote", total_fare=999.99)
    events = [engine_event(json.dumps(stale), invocation_id="e-older")]
    assert extract_fare_quote(events, "e-current") is None


def test_extract_prefers_current_invocation_over_stale():
    stale = dict(ENGINE_QUOTE, quote_id="stale-quote", total_fare=999.99)
    events = [
        engine_event(json.dumps(stale), invocation_id="e-older"),
        engine_event(json.dumps(ENGINE_QUOTE), invocation_id="e-current"),
    ]
    assert extract_fare_quote(events, "e-current") == ENGINE_QUOTE


def test_extract_latest_engine_event_wins_within_invocation():
    older = dict(ENGINE_QUOTE, quote_id="first-quote")
    events = [
        engine_event(json.dumps(older)),
        engine_event(json.dumps(ENGINE_QUOTE)),
    ]
    assert extract_fare_quote(events, "e-current")["quote_id"] == ENGINE_QUOTE[
        "quote_id"
    ]


def test_extract_ignores_other_authors():
    events = [engine_event(json.dumps(ENGINE_QUOTE), author="policy_agent")]
    assert extract_fare_quote(events, "e-current") is None


def test_extract_no_events_returns_none():
    assert extract_fare_quote([], "e-current") is None


def test_extract_non_json_engine_text_returns_none():
    events = [engine_event("A2A request failed: 403 Forbidden")]
    assert extract_fare_quote(events, "e-current") is None


def test_extract_json_error_object_is_not_a_quote():
    events = [engine_event(json.dumps({"error": "could not price trip"}))]
    assert extract_fare_quote(events, "e-current") is None


def test_extract_event_without_content_returns_none():
    events = [Event(invocation_id="e-current", author=FARE_ENGINE_AUTHOR)]
    assert extract_fare_quote(events, "e-current") is None


# --- parse_policy_decision ---


def test_parse_plain_json_decision():
    decision = parse_policy_decision(POLICY_APPROVED_TEXT)
    assert decision["status"] == "approved"
    assert decision["requires_manager_approval"] is False


def test_parse_fenced_json_decision():
    fenced = f"```json\n{POLICY_APPROVED_TEXT}\n```"
    assert parse_policy_decision(fenced)["status"] == "approved"


def test_parse_prose_wrapped_json_decision():
    wrapped = f"Here is the decision: {POLICY_APPROVED_TEXT} Let me know."
    assert parse_policy_decision(wrapped)["status"] == "approved"


@pytest.mark.parametrize(
    "garbage",
    [
        None,
        "",
        "   ",
        "not json at all",
        json.dumps({"status": "maybe"}),  # invalid Status literal
        json.dumps(["approved"]),  # not an object
        {"status": "approved"},  # already a dict, not text
    ],
)
def test_parse_garbage_returns_none(garbage):
    assert parse_policy_decision(garbage) is None


# --- assemble_output ---


def test_assemble_full_healthy_record():
    output = assemble_output(
        intake_output=INTAKE_READY,
        policy_text=POLICY_APPROVED_TEXT,
        fare_quote=ENGINE_QUOTE,
        summary="Trip approved.",
    )
    assert output.traveler == INTAKE_READY["traveler"]
    assert output.trip == INTAKE_READY["trip"]
    assert output.fare_quote == ENGINE_QUOTE
    assert output.final_decision == "approved"
    assert output.summary == "Trip approved."


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("approved", "approved"),
        ("denied", "denied"),
        ("needs_review", "needs_review"),
    ],
)
def test_final_decision_mirrors_policy_status(status, expected):
    policy_text = json.dumps({"status": status})
    output = assemble_output(
        intake_output=INTAKE_READY,
        policy_text=policy_text,
        fare_quote=None,
        summary="s",
    )
    assert output.final_decision == expected
    assert output.policy_decision["status"] == status


def test_not_ready_for_policy_is_incomplete():
    intake = dict(INTAKE_READY, ready_for_policy=False, missing_fields=["trip.origin"])
    output = assemble_output(
        intake_output=intake,
        policy_text=json.dumps({"status": "needs_review"}),
        fare_quote=None,
        summary="s",
    )
    assert output.final_decision == "incomplete"
    # A parseable policy decision is still recorded for the audit trail.
    assert output.policy_decision["status"] == "needs_review"


def test_not_ready_with_missing_policy_keeps_policy_none():
    intake = dict(INTAKE_READY, ready_for_policy=False)
    output = assemble_output(
        intake_output=intake, policy_text=None, fare_quote=None, summary="s"
    )
    assert output.final_decision == "incomplete"
    assert output.policy_decision is None


def test_unparseable_policy_while_ready_degrades_to_needs_review():
    output = assemble_output(
        intake_output=INTAKE_READY,
        policy_text="the model rambled instead of emitting JSON",
        fare_quote=ENGINE_QUOTE,
        summary="s",
    )
    assert output.final_decision == "needs_review"
    assert output.policy_decision["status"] == "needs_review"
    assert output.policy_decision["requires_manager_approval"] is True
    assert any("unparseable" in r for r in output.policy_decision["reasons"])


def test_missing_intake_output_is_incomplete_with_empty_dicts():
    output = assemble_output(
        intake_output=None, policy_text=None, fare_quote=None, summary="s"
    )
    assert output.final_decision == "incomplete"
    assert output.traveler == {}
    assert output.trip == {}
    assert output.fare_quote is None


@pytest.mark.parametrize("summary", [None, "", "   \n"])
def test_empty_summary_falls_back_to_deterministic_line(summary):
    output = assemble_output(
        intake_output=INTAKE_READY,
        policy_text=POLICY_APPROVED_TEXT,
        fare_quote=ENGINE_QUOTE,
        summary=summary,
    )
    assert output.summary  # min_length=1 must never throw
    assert "approved" in output.summary


def test_summary_is_stripped_verbatim_otherwise():
    output = assemble_output(
        intake_output=INTAKE_READY,
        policy_text=POLICY_APPROVED_TEXT,
        fare_quote=None,
        summary="  Trip approved within policy.  ",
    )
    assert output.summary == "Trip approved within policy."


# --- FinalizerAssembler agent ---


def run_assembler(session: Session) -> tuple[list[Event], InvocationContext]:
    agent = FinalizerAssembler(name="finalizer_assembler")
    ctx = InvocationContext(
        session_service=InMemorySessionService(),
        invocation_id="e-current",
        agent=agent,
        session=session,
    )

    async def collect() -> list[Event]:
        return [event async for event in agent.run_async(ctx)]

    return asyncio.run(collect()), ctx


def test_agent_emits_single_event_with_state_delta_and_verbatim_quote():
    stale = dict(ENGINE_QUOTE, quote_id="stale-quote", total_fare=999.99)
    session = Session(
        id="s1",
        app_name="travel-prequal-test",
        user_id="u1",
        state={
            "intake_output": INTAKE_READY,
            "policy_decision": POLICY_APPROVED_TEXT,
            SUMMARY_STATE_KEY: "Approved: JFK-LAX round trip for $288.90.",
        },
        events=[
            engine_event(json.dumps(stale), invocation_id="e-older"),
            engine_event(json.dumps(ENGINE_QUOTE)),
        ],
    )
    events, _ = run_assembler(session)

    assert len(events) == 1
    event = events[0]
    assert event.author == "finalizer_assembler"
    assert event.invocation_id == "e-current"

    stored = event.actions.state_delta[OUTPUT_STATE_KEY]
    emitted = json.loads(event.content.parts[0].text)
    assert emitted == stored
    assert stored["fare_quote"] == ENGINE_QUOTE  # the regression, end to end
    assert stored["final_decision"] == "approved"
    assert stored["summary"] == "Approved: JFK-LAX round trip for $288.90."


def test_agent_outage_path_no_quote_still_never_approves_blind():
    session = Session(
        id="s2",
        app_name="travel-prequal-test",
        user_id="u1",
        state={
            "intake_output": INTAKE_READY,
            "policy_decision": json.dumps(
                {
                    "status": "needs_review",
                    "reasons": ["no fare quote available; budget cannot be verified"],
                    "requires_manager_approval": True,
                }
            ),
            SUMMARY_STATE_KEY: "",
        },
        events=[engine_event("A2A request failed: 403 Forbidden")],
    )
    events, _ = run_assembler(session)

    stored = events[0].actions.state_delta[OUTPUT_STATE_KEY]
    assert stored["fare_quote"] is None
    assert stored["final_decision"] == "needs_review"
    assert stored["policy_decision"]["requires_manager_approval"] is True
    assert stored["summary"]  # deterministic fallback filled in
