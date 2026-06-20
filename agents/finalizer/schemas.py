"""The system's final output schema.

It lives here, in the finalizer package, because the finalizer is the agent that
produces it (via `output_schema`). Defining it here — rather than in the
orchestrator package — avoids a circular import: the orchestrator package imports
the finalizer agent, so the finalizer must NOT import back from the orchestrator
package. `agents/orchestrator/schemas.py` re-exports these for convenience.
"""

from typing import Literal

from pydantic import BaseModel, Field

FinalDecision = Literal["approved", "denied", "needs_review", "incomplete"]


class TravelQualificationOutput(BaseModel):
    traveler: dict
    trip: dict
    policy_decision: dict | None = None
    fare_quote: dict | None = None
    final_decision: FinalDecision
    summary: str = Field(min_length=1)
