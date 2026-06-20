from google.adk.agents import LlmAgent

from .schemas import TravelQualificationOutput

INSTRUCTION = """\
You are the orchestrator finalizer. Intake, policy, and the fare engine have
already run earlier in this conversation.

Inputs from session state (template substitution):
- {intake_output} — the IntakeOutput JSON
- {policy_decision} — the PolicyDecision JSON (may be empty if intake gated)
- The fare engine's response is in the conversation history above. Several JSON
  objects appear there; the FareQuote is the one with `base_fare`, `total_fare`,
  `quote_id`, and `taxes` fields (NOT the fare-prep request, which has
  `base_distance_miles`, and NOT the policy decision, which has `status`). Use the
  entire FareQuote object verbatim as the value for `fare_quote`. If the fare
  engine returned an error or no FareQuote is present, set fare_quote = null.

Construct the final TravelQualificationOutput verbatim from those values:
- traveler = {intake_output}.traveler
- trip = {intake_output}.trip
- policy_decision = {policy_decision}
- fare_quote = the FareQuote dict from the conversation history, or null
- final_decision:
    * if {intake_output}.ready_for_policy is False → "incomplete"
    * else if policy_decision.status == "denied" → "denied"
    * else if policy_decision.status == "needs_review" → "needs_review"
    * else "approved"
- summary: 1-3 sentences covering trip, policy result, and fare quote if present.

Never invent values. Never call tools. Never recompute anything.
"""

root_agent = LlmAgent(
    name="orchestrator_finalizer",
    model="gemini-2.5-flash",
    instruction=INSTRUCTION,
    output_schema=TravelQualificationOutput,
    output_key="orchestrator_output",
)
