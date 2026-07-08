"""Deterministic assembly of the final PreTripApprovalOutput.

This is the finalizer's structured half: pure functions that build the
approval record from session state plus the current invocation's events,
wrapped in a model-free BaseAgent. The LLM half (`summary_writer` in
agent.py) contributes exactly one field - the prose `summary`. Every other
field already exists as data produced upstream, so routing it through a
model added only transcription variance (the production failure this module
removes: fare_rules arriving populated from the engine and leaving the
finalizer as null). Mirrors the engine's quote-passthrough pattern
(travel-fare-engine cmd/server/passthrough.go).
"""

import json
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types
from pydantic import ValidationError

from agents.policy.schemas import PolicyDecision

from .schemas import PreTripApprovalOutput

# Must match the RemoteA2aAgent name in agents/orchestrator/agent.py (the
# finalizer package cannot import it back - see schemas.py on the circular
# import). A rename there would make every fare_quote null; the E2E smoke
# test catches that immediately.
FARE_ENGINE_AUTHOR = "fare_engine"

SUMMARY_STATE_KEY = "approval_summary"
OUTPUT_STATE_KEY = "orchestrator_output"


def extract_fare_quote(
    events: Sequence[Event], invocation_id: str
) -> dict | None:
    """Return the FareQuote dict from THIS invocation's fare_engine response.

    Scanning is invocation-scoped: a quote from an earlier turn in the same
    session must never leak into a later run (the stale-quote fix). The
    `total_fare` marker check keeps a JSON-shaped engine error from being
    mistaken for a quote. On absence or any parse failure, returns None -
    the record then carries fare_quote=null rather than a guess.
    """
    for event in reversed(events):
        if event.invocation_id != invocation_id:
            continue
        if event.author != FARE_ENGINE_AUTHOR:
            continue
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if not part.text or part.thought:
                continue
            try:
                obj = json.loads(part.text)
            except ValueError:
                continue
            if isinstance(obj, dict) and "total_fare" in obj:
                return obj
    return None


def parse_policy_decision(policy_text: Any) -> dict | None:
    """Parse the JSON-as-text PolicyDecision the policy agent stored.

    The policy agent emits JSON as plain text (it cannot use output_schema,
    which would disable its tools). Tolerates a fenced or prose-wrapped
    object by retrying on the outermost brace span. Returns None when no
    valid PolicyDecision can be recovered; the caller decides how to degrade.
    """
    if not isinstance(policy_text, str) or not policy_text.strip():
        return None
    text = policy_text.strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            return PolicyDecision.model_validate_json(candidate).model_dump()
        except ValidationError:
            continue
    return None


def _needs_review_fallback() -> dict:
    """PolicyDecision to record when policy output is missing or garbage.

    Never approve on an unreadable decision - same stance as the
    malformed-counts-as-fail rule in agents/policy/rules.py.
    """
    return PolicyDecision(
        status="needs_review",
        reasons=["policy decision missing or unparseable; manual review required"],
        requires_manager_approval=True,
    ).model_dump()


def assemble_output(
    *,
    intake_output: Any,
    policy_text: Any,
    fare_quote: dict | None,
    summary: Any,
) -> PreTripApprovalOutput:
    """Build the final approval record from upstream data, deterministically.

    final_decision is derived entirely here: "incomplete" while intake has
    not released the trip to policy, otherwise a mirror of the policy status
    (degraded to needs_review when the policy text is unreadable). No LLM is
    on this path.
    """
    intake = intake_output if isinstance(intake_output, dict) else {}
    ready_for_policy = bool(intake.get("ready_for_policy"))
    policy = parse_policy_decision(policy_text)

    if not ready_for_policy:
        final_decision = "incomplete"
    elif policy is None:
        policy = _needs_review_fallback()
        final_decision = "needs_review"
    else:
        final_decision = policy["status"]

    summary_text = summary.strip() if isinstance(summary, str) else ""
    if not summary_text:
        summary_text = f"Pre-trip approval processed; final decision: {final_decision}."

    return PreTripApprovalOutput(
        traveler=intake.get("traveler") or {},
        trip=intake.get("trip") or {},
        policy_decision=policy,
        fare_quote=fare_quote,
        final_decision=final_decision,
        summary=summary_text,
    )


class FinalizerAssembler(BaseAgent):
    """Model-free agent that emits the assembled PreTripApprovalOutput.

    Yields a single text event carrying the record as JSON (the pipeline's
    last event, the position the LlmAgent finalizer used to fill) and writes
    the dict into session state under `orchestrator_output` via
    actions.state_delta - the same mechanism LlmAgent's output_key uses.
    """

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        output = assemble_output(
            intake_output=state.get("intake_output"),
            policy_text=state.get("policy_decision"),
            fare_quote=extract_fare_quote(ctx.session.events, ctx.invocation_id),
            summary=state.get(SUMMARY_STATE_KEY),
        )
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(
                role="model",
                parts=[types.Part(text=output.model_dump_json(indent=2))],
            ),
            actions=EventActions(
                state_delta={OUTPUT_STATE_KEY: output.model_dump()}
            ),
        )
