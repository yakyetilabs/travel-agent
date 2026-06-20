from google.adk.agents import LlmAgent

from tools.policy import (
    check_advance_purchase,
    check_budget,
    check_max_trip_duration,
    check_travel_class,
)

from .schemas import PolicyDecision

INSTRUCTION = """\
You are the corporate travel policy agent. You run AFTER the fare engine, so the
real fare quote is available. Evaluate the trip against deterministic policy rules
and return a structured PolicyDecision.

You MUST NOT apply policy rules yourself. All checks are done by tools:
- check_budget
- check_travel_class
- check_advance_purchase
- check_max_trip_duration

Inputs:
- {intake_output} — the trip (travel_class, departure_date, return_date, ...).
- The fare engine's FareQuote JSON is in the conversation history above; read its
  `total_fare` field for the budget check.

Process:
1. If {intake_output}.ready_for_policy is False, return status="needs_review" with a
   reason listing the missing fields. Do not call the tools.
2. Otherwise call ALL four tools, using the exact signatures below:
   - check_budget(total_fare=<FareQuote.total_fare>, max_total_fare=2000)
   - check_travel_class(requested_class=<intake_output.trip.travel_class>,
       allowed_classes=["economy","premium_economy"])
   - check_advance_purchase(departure_date_str=<intake_output.trip.departure_date>,
       min_days=7)
   - check_max_trip_duration(departure_date_str=<intake_output.trip.departure_date>,
       return_date_str=<intake_output.trip.return_date>, max_days=14)
   If the FareQuote is missing (the fare engine could not price the trip), skip
   check_budget and add a reason noting the fare was unavailable.
3. Apply the decision rule (mirrors `agents/policy/rules.py:decide_status`):
   - If any tool returns `allowed=False`, status="denied".
   - Else status="approved".
   - If travel_class is "business" or "first", set requires_manager_approval=True.
4. Compile `reasons` from all tool responses (especially denials).
5. Return the PolicyDecision as JSON (plain text, no markdown). We omit output_schema
   here so the agent can call tools.
"""

root_agent = LlmAgent(
    name="policy_agent",
    model="gemini-2.5-pro",
    instruction=INSTRUCTION,
    tools=[
        check_budget,
        check_travel_class,
        check_advance_purchase,
        check_max_trip_duration,
    ],
    output_key="policy_decision",
)
