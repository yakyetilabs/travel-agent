from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["approved", "denied", "needs_review"]


class PolicyDecision(BaseModel):
    status: Status
    reasons: list[str] = Field(default_factory=list)
    requires_manager_approval: bool = False
