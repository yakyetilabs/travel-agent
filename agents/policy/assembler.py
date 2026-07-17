"""Deterministic assembly of the PolicyDecision.

The policy stage's structured half: the LLM (`policy_checks` in agent.py)
only routes to the deterministic check tools; this module reads those tools'
results from the current invocation's events and assembles the PolicyDecision
in pure Python via the canonical decision rule (rules.py, previously a
reference implementation, now the runtime path). The production trigger: the
transcribing LlmAgent probabilistically dropped reason strings from its JSON
(CI eval failure 2026-07-16, response_match 0.47 vs 0.5) - the same lossy
LLM-copy class the finalizer split removed (docs/DECISIONS.md §9, §4).
"""

from collections.abc import AsyncGenerator, Sequence
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types

from .rules import decide_status, needs_manager_approval
from .schemas import PolicyDecision

# Canonical order: the order the prompt instructs the checks be called, and
# the order every evalset reference lists its reasons.
POLICY_TOOLS: tuple[str, ...] = (
    "check_budget",
    "check_travel_class",
    "check_advance_purchase",
    "check_max_trip_duration",
)

# Must stay a JSON *string* under this key: the finalizer's
# parse_policy_decision consumes state["policy_decision"] exactly as
# LlmAgent's output_key used to store it (agents/finalizer/assembler.py).
DECISION_STATE_KEY = "policy_decision"

ONE_WAY_DURATION_REASON = "one-way trip: duration limit not applicable"


def extract_tool_results(
    events: Sequence[Event], invocation_id: str
) -> dict[str, dict]:
    """Map each policy tool to its response from THIS invocation's events.

    Invocation-scoped for the same reason the finalizer's quote extraction
    is: results from an earlier turn in the same session must never bleed
    into a later decision. Latest-wins per tool if a check somehow ran twice.
    Non-policy function responses (e.g. the engine's compute_fare) are
    ignored by name.
    """
    results: dict[str, dict] = {}
    for event in events:
        if event.invocation_id != invocation_id:
            continue
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            response = part.function_response
            if response is None or response.name not in POLICY_TOOLS:
                continue
            if isinstance(response.response, dict):
                results[response.name] = response.response
    return results


def assemble_decision(
    *, intake_output: Any, tool_results: dict[str, dict]
) -> PolicyDecision:
    """Build the PolicyDecision from the tool results, deterministically.

    Status and the manager-approval flag come from rules.py (empty results
    degrade to needs_review, never an approval). Reasons are one verbatim
    tool `reason` per executed check, in canonical order; a one_way trip that
    legitimately skipped the duration check records why; a run where no
    checks fired records what intake is still missing.
    """
    intake = intake_output if isinstance(intake_output, dict) else {}
    reasons = [
        str(tool_results[name].get("reason", f"{name}: no reason recorded"))
        for name in POLICY_TOOLS
        if name in tool_results
    ]

    trip = intake.get("trip") or {}
    if (
        tool_results
        and "check_max_trip_duration" not in tool_results
        and trip.get("trip_type") == "one_way"
    ):
        reasons.append(ONE_WAY_DURATION_REASON)

    if not tool_results:
        missing = [str(m) for m in intake.get("missing_fields") or []]
        if missing:
            reasons.append("intake incomplete; missing fields: " + ", ".join(missing))
        else:
            reasons.append("no policy checks ran; manual review required")

    return PolicyDecision(
        status=decide_status(tool_results),
        reasons=reasons,
        requires_manager_approval=needs_manager_approval(tool_results),
    )


class PolicyAssembler(BaseAgent):
    """Model-free agent that emits the assembled PolicyDecision.

    Yields a single text event carrying the decision as compact JSON (the
    stage's last event, the position the transcribing LlmAgent used to fill)
    and writes the SAME JSON string into session state under
    `policy_decision` via actions.state_delta, preserving the downstream
    contract (see DECISION_STATE_KEY).
    """

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        decision = assemble_decision(
            intake_output=ctx.session.state.get("intake_output"),
            tool_results=extract_tool_results(
                ctx.session.events, ctx.invocation_id
            ),
        )
        decision_json = decision.model_dump_json()
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(
                role="model",
                parts=[types.Part(text=decision_json)],
            ),
            actions=EventActions(
                state_delta={DECISION_STATE_KEY: decision_json}
            ),
        )
