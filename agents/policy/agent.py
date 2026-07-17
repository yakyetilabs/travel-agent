"""Policy stage: the LLM routes to checks; code assembles the decision.

Split design, same as the finalizer (docs/DECISIONS.md §9, §4):

- `policy_checks` (LlmAgent) owns only the judgment the evals pin - which
  deterministic check tools to call, with which arguments, including the
  no-argument `check_budget` call when the engine produced no quote.
- `policy_assembler` (PolicyAssembler, no model) owns the decision rule
  (rules.py, now the runtime path rather than a mirrored prose copy), the
  verbatim reason list, and the PolicyDecision JSON - see assembler.py.

The previous single-LlmAgent policy stage transcribed the tool results into
JSON itself and probabilistically dropped reason strings in transit (CI eval
failure 2026-07-16). Assembly in code removes that copy path instead of
supervising it.

`root_agent` stays a single importable node named "policy_agent" (an ADK
Workflow graph) so the orchestrator pipeline and `adk run agents/policy` are
unchanged.
"""

from google.adk.agents import LlmAgent
from google.adk.workflow import START, Workflow

from agents.model import gemini_flash
from tools.policy import (
    check_advance_purchase,
    check_budget,
    check_max_trip_duration,
    check_travel_class,
)

from .assembler import PolicyAssembler

INSTRUCTION = """\
You are the corporate travel policy checks runner. You run AFTER the fare
engine, so the real fare quote is available. Your ONLY job is to call the
deterministic check tools with the right arguments; a deterministic assembler
(not you) applies the decision rule and writes the PolicyDecision.

You MUST NOT apply policy rules yourself. Every policy threshold (budget cap,
cabin policy, advance-purchase minimum, duration limit) is a constant inside
tools/policy.py; the tools take only trip and fare data, never thresholds.

Inputs:
- {intake_output} - the trip (travel_class, trip_type, departure_date,
  return_date, ...).
- The fare engine's FareQuote JSON is in the conversation history above; read
  its `total_fare` field for the budget check. The budget cap is a TRIP budget
  cap: it applies to the full journey total (both legs of a round trip, all
  passengers on the booking, guests included), which is exactly what
  `total_fare` is.

Process:
1. If {intake_output}.ready_for_policy is False, call NO tools and reply with
   the single word: incomplete. The assembler records the escalation.
2. Otherwise call the tools, using the exact signatures below:
   - check_budget(total_fare=<FareQuote.total_fare>). If no FareQuote exists in
     the conversation (the fare engine failed or could not price the trip),
     call check_budget with NO arguments instead. NEVER skip check_budget: an
     unverifiable budget is a policy verdict the tool records, not a footnote.
   - check_travel_class(requested_class=<intake_output.trip.travel_class>)
   - check_advance_purchase(departure_date_str=<intake_output.trip.departure_date>)
   - check_max_trip_duration(departure_date_str=<intake_output.trip.departure_date>,
       return_date_str=<intake_output.trip.return_date>)
     ONLY when trip_type is "round_trip". For a one_way trip there is no return
     date, so skip this tool. A same-day round trip is a legitimate day trip;
     let the tool judge it.
3. After the tools return, reply with the single word: done. Do not summarize,
   restate, or judge the results - the assembler owns the decision.
"""

policy_checks = LlmAgent(
    name="policy_checks",
    model=gemini_flash,
    instruction=INSTRUCTION,
    tools=[
        check_budget,
        check_travel_class,
        check_advance_purchase,
        check_max_trip_duration,
    ],
)

# A two-node Workflow graph, mirroring the finalizer: the chain tuple expands
# to START -> policy_checks -> policy_assembler, and the assembler, being the
# terminal node, owns the PolicyDecision the stage emits.
root_agent = Workflow(
    name="policy_agent",
    edges=[(START, policy_checks, PolicyAssembler(name="policy_assembler"))],
)
