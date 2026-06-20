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
